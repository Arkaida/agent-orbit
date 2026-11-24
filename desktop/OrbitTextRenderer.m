#import "OrbitTextRenderer.h"
#import <CoreText/CoreText.h>
#import <os/log.h>
#include <math.h>
#include <pthread.h>
#include <string.h>
#include <time.h>

static const CGFloat AOCharacterSpacing = 0.25;
static const NSUInteger AOMaxUTF16Length = 512;
static const NSUInteger AOMaxCharacters = 256;

#if defined(ORBIT_TEXT_TESTING)
static AOTextTestFault AOTestFault;
static NSUInteger AODiagnostics;
static NSUInteger AOPlaceholders;
void AOTextTestInjectFault(AOTextTestFault fault) { AOTestFault = fault; }
NSUInteger AOTextTestDiagnosticCount(void) { return AODiagnostics; }
NSUInteger AOTextTestPlaceholderCount(void) { return AOPlaceholders; }
static void AOInject(AOTextTestFault fault) {
    if (AOTestFault == fault) {
        AOTestFault = AOTextTestFaultNone;
        [NSException raise:NSInvalidArgumentException format:@"Synthetic Orbit text fault"];
    }
}
#define AO_FAULT(stage) AOInject(AOTextTestFault##stage)
#else
#define AO_FAULT(stage) ((void)0)
#endif

static BOOL AOShouldLog(const char *stage) {
    typedef struct { const char *stage; double last; } Entry;
    static Entry entries[24];
    static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
    struct timespec clock;
    double now = clock_gettime(CLOCK_MONOTONIC, &clock) == 0 ? clock.tv_sec + clock.tv_nsec / 1e9 : 0;
    BOOL emit = NO;
    pthread_mutex_lock(&lock);
    for (NSUInteger index = 0; index < sizeof(entries) / sizeof(entries[0]); index += 1) {
        if (entries[index].stage == NULL) {
            entries[index] = (Entry){stage, now}; emit = YES; break;
        }
        if (strcmp(entries[index].stage, stage) == 0) {
            if (now - entries[index].last >= 5) { entries[index].last = now; emit = YES; }
            break;
        }
    }
    pthread_mutex_unlock(&lock);
    return emit;
}

// Never include the string, exception reason, font descriptor, or session name.
static void AOFailure(const char *stage, NSException *exception, CGFloat size, NSUInteger count) {
#if defined(ORBIT_TEXT_TESTING)
    AODiagnostics += 1;
#endif
    if (!AOShouldLog(stage)) { return; }
    const char *category = exception == nil ? "invalid_input" :
        [exception.name isEqualToString:NSInvalidArgumentException] ? "invalid_argument" : "objc_exception";
    os_log_error(OS_LOG_DEFAULT, "Orbit text failure stage=%{public}s category=%{public}s font_size=%{public}.2f characters=%{public}lu",
                 stage, category, (double)(isfinite(size) ? size : 0), (unsigned long)count);
}

static BOOL AOFinitePoint(CGPoint point) {
    return isfinite(point.x) && isfinite(point.y) && fabs(point.x) <= 1e7 && fabs(point.y) <= 1e7;
}

static BOOL AOFiniteRect(CGRect rect) {
    return AOFinitePoint(rect.origin) && isfinite(rect.size.width) && isfinite(rect.size.height) &&
        rect.size.width >= 0 && rect.size.height >= 0 && rect.size.width <= 1e7 && rect.size.height <= 1e7;
}

static void AOPlaceholder(CGContextRef context, CGRect rect) {
    if (context == NULL || !AOFiniteRect(rect) || rect.size.width <= 0 || rect.size.height <= 0) { return; }
#if defined(ORBIT_TEXT_TESTING)
    AOPlaceholders += 1;
#endif
    CGContextSaveGState(context);
    @try {
        CGFloat width = fmin(24, fmax(4, rect.size.width - 2));
        CGFloat height = fmin(7, fmax(3, rect.size.height - 2));
        CGRect mark = CGRectMake(CGRectGetMidX(rect) - width / 2, CGRectGetMidY(rect) - height / 2, width, height);
        CGContextSetRGBStrokeColor(context, 0.65, 0.69, 0.74, 0.9);
        CGContextSetLineWidth(context, 0.9);
        CGContextStrokeRect(context, mark);
        CGContextMoveToPoint(context, CGRectGetMinX(mark) + 2, CGRectGetMidY(mark));
        CGContextAddLineToPoint(context, CGRectGetMaxX(mark) - 2, CGRectGetMidY(mark));
        CGContextStrokePath(context);
    } @finally {
        CGContextRestoreGState(context);
    }
}

static CGColorRef AOCopyColor(NSColor *color) CF_RETURNS_RETAINED;
static CGColorRef AOCopyColor(NSColor *color) {
    AO_FAULT(Color);
    if (![color isKindOfClass:NSColor.class]) { return NULL; }
    NSColor *resolved = [color colorUsingColorSpace:NSColorSpace.sRGBColorSpace];
    CGColorRef cgColor = resolved.CGColor;
    return cgColor == NULL ? NULL : CGColorCreateCopy(cgColor);
}

@implementation AOTextLayout {
    CTLineRef _line;
    NSArray *_characterLines;
    NSArray<NSNumber *> *_characterWidths;
    CGFloat _width;
    CGFloat _arcWidth;
    CGFloat _height;
    CGFloat _ascender;
    CGFloat _descender;
    CGFloat _fontSize;
    NSUInteger _characterCount;
    BOOL _isUsable;
}

@synthesize width = _width, arcWidth = _arcWidth, height = _height;
@synthesize ascender = _ascender, characterCount = _characterCount, isUsable = _isUsable;

+ (instancetype)layoutWithText:(NSString *)text fontSize:(CGFloat)fontSize {
    AOTextLayout *layout = [[self alloc] init];
    layout->_fontSize = isfinite(fontSize) && fontSize >= 0.5 && fontSize <= 256 ? fontSize : 10;
    layout->_height = ceil(layout->_fontSize * 1.3);
    layout->_ascender = layout->_fontSize;
    layout->_descender = layout->_fontSize * 0.3;
    layout->_width = layout->_arcWidth = 18;
    CTFontRef font = NULL;
    @try {
        if (![text isKindOfClass:NSString.class] || !isfinite(fontSize) || fontSize < 0.5 || fontSize > 256 || text.length > AOMaxUTF16Length) {
            AOFailure("layout_input", nil, fontSize, 0);
            return layout;
        }
        AO_FAULT(Layout);
        // Replace only terminal control ranges. Preserve Unicode composition,
        // emoji variation selectors, joiners, and normal non-ASCII names.
        NSMutableString *safeText = [text mutableCopy];
        for (NSUInteger index = 0; index < safeText.length; index += 1) {
            unichar value = [safeText characterAtIndex:index];
            if (value <= 0x1f || (value >= 0x7f && value <= 0x9f)) {
                [safeText replaceCharactersInRange:NSMakeRange(index, 1) withString:@" "];
            }
        }
        NSMutableArray<NSString *> *characters = [NSMutableArray array];
        [safeText enumerateSubstringsInRange:NSMakeRange(0, safeText.length)
                                    options:NSStringEnumerationByComposedCharacterSequences
                                 usingBlock:^(NSString *substring, NSRange range, NSRange enclosing, BOOL *stop) {
            if (substring != nil) { [characters addObject:substring]; }
            if (characters.count > AOMaxCharacters) { *stop = YES; }
        }];
        layout->_characterCount = characters.count;
        if (characters.count > AOMaxCharacters) {
            AOFailure("layout_length", nil, fontSize, characters.count);
            return layout;
        }
        font = CTFontCreateWithName(CFSTR("Menlo"), fontSize, NULL);
        if (font == NULL) { AOFailure("font", nil, fontSize, characters.count); return layout; }
        // Bypass NSString/AppKit measuring with a concrete CTFont and immutable
        // attributes. CoreText font fallback can still fail and stays protected.
        NSDictionary *attributes = @{(__bridge NSString *)kCTFontAttributeName: (__bridge id)font,
            (__bridge NSString *)kCTForegroundColorFromContextAttributeName: @YES};
        NSAttributedString *full = [[NSAttributedString alloc] initWithString:safeText attributes:attributes];
        layout->_line = CTLineCreateWithAttributedString((__bridge CFAttributedStringRef)full);
        if (layout->_line == NULL) { AOFailure("line", nil, fontSize, characters.count); return layout; }
        CGFloat ascent = 0, descent = 0, leading = 0;
        CGFloat width = (CGFloat)CTLineGetTypographicBounds(layout->_line, &ascent, &descent, &leading);
        if (!isfinite(width) || !isfinite(ascent) || !isfinite(descent) || !isfinite(leading) || width < 0 || width > 1e6) {
            AOFailure("layout_metrics", nil, fontSize, characters.count); return layout;
        }
        NSMutableArray *lines = [NSMutableArray arrayWithCapacity:characters.count];
        NSMutableArray<NSNumber *> *widths = [NSMutableArray arrayWithCapacity:characters.count];
        CGFloat arcWidth = 0;
        for (NSString *character in characters) {
            NSAttributedString *part = [[NSAttributedString alloc] initWithString:character attributes:attributes];
            CTLineRef line = CTLineCreateWithAttributedString((__bridge CFAttributedStringRef)part);
            if (line == NULL) { AOFailure("character_line", nil, fontSize, characters.count); return layout; }
            // Transfer ownership before any later operation can throw.
            id heldLine = CFBridgingRelease(line);
            CGFloat advance = (CGFloat)CTLineGetTypographicBounds((__bridge CTLineRef)heldLine, NULL, NULL, NULL);
            if (!isfinite(advance) || advance < 0 || advance > 1e6) {
                AOFailure("character_metrics", nil, fontSize, characters.count); return layout;
            }
            [lines addObject:heldLine];
            [widths addObject:@(advance)];
            arcWidth += advance + AOCharacterSpacing;
        }
        if (!isfinite(arcWidth) || arcWidth > 1e6) {
            AOFailure("arc_metrics", nil, fontSize, characters.count); return layout;
        }
        layout->_characterLines = [lines copy];
        layout->_characterWidths = [widths copy];
        layout->_width = width;
        layout->_arcWidth = arcWidth;
        layout->_ascender = ascent;
        layout->_descender = descent;
        layout->_height = ceil(fmax(1, ascent + descent + fmax(0, leading)));
        layout->_isUsable = YES;
    } @catch (NSException *exception) {
        AOFailure("layout", exception, fontSize, layout->_characterCount);
    } @finally {
        if (font != NULL) { CFRelease(font); }
    }
    return layout;
}

- (void)dealloc {
    if (_line != NULL) { CFRelease(_line); }
}

- (BOOL)drawInContext:(CGContextRef)context rect:(CGRect)rect color:(NSColor *)color centered:(BOOL)centered {
    if (context == NULL || !AOFiniteRect(rect)) { AOFailure("flat_geometry", nil, _fontSize, _characterCount); return NO; }
    if (!_isUsable) { AOPlaceholder(context, rect); return NO; }
    BOOL success = NO;
    CGColorRef resolved = NULL;
    CGAffineTransform originalTextMatrix = CGContextGetTextMatrix(context);
    CGPoint originalTextPosition = CGContextGetTextPosition(context);
    CGContextSaveGState(context);
    @try {
        resolved = AOCopyColor(color);
        if (resolved == NULL) { AOFailure("flat_color", nil, _fontSize, _characterCount); }
        else {
            CGContextClipToRect(context, rect);
            CGContextSetFillColorWithColor(context, resolved);
            CGContextSetTextMatrix(context, CGAffineTransformIdentity);
            CGFloat x = centered ? CGRectGetMidX(rect) - _width / 2 : CGRectGetMinX(rect);
            CGFloat y = CGRectGetMidY(rect) - (_ascender - _descender) / 2;
            CGContextSetTextPosition(context, x, y);
            AO_FAULT(FlatDraw);
            CTLineDraw(_line, context);
            success = YES;
        }
    } @catch (NSException *exception) {
        AOFailure("flat_draw", exception, _fontSize, _characterCount);
    } @finally {
        CGContextRestoreGState(context);
        CGContextSetTextMatrix(context, originalTextMatrix);
        CGContextSetTextPosition(context, originalTextPosition.x, originalTextPosition.y);
        if (resolved != NULL) { CGColorRelease(resolved); }
    }
    if (!success) { AOPlaceholder(context, rect); }
    return success;
}

- (BOOL)drawArcInContext:(CGContextRef)context center:(CGPoint)center radius:(CGFloat)radius angle:(CGFloat)angle color:(NSColor *)color {
    if (context == NULL || !AOFinitePoint(center) || !isfinite(radius) || radius <= 0 || radius > 1e6 || !isfinite(angle) || fabs(angle) > 1e9) {
        AOFailure("arc_geometry", nil, _fontSize, _characterCount); return NO;
    }
    CGPoint anchor = CGPointMake(center.x + cos(angle) * radius, center.y + sin(angle) * radius);
    CGRect placeholder = CGRectMake(anchor.x - 12, anchor.y - 5, 24, 10);
    if (!_isUsable) { AOPlaceholder(context, placeholder); return NO; }
    BOOL success = NO;
    CGColorRef resolved = NULL;
    CGAffineTransform originalTextMatrix = CGContextGetTextMatrix(context);
    CGPoint originalTextPosition = CGContextGetTextPosition(context);
    CGContextSaveGState(context);
    @try {
        resolved = AOCopyColor(color);
        if (resolved == NULL) { AOFailure("arc_color", nil, _fontSize, _characterCount); }
        else {
            CGContextSetFillColorWithColor(context, resolved);
            CGContextSetTextMatrix(context, CGAffineTransformIdentity);
            BOOL bottom = sin(remainder(angle, M_PI * 2)) < -0.1;
            CGFloat offset = -_arcWidth / 2;
            for (NSUInteger index = 0; index < _characterLines.count; index += 1) {
                CGFloat advance = _characterWidths[index].doubleValue;
                CGFloat width = advance + AOCharacterSpacing;
                CGFloat delta = (offset + width / 2) / radius;
                CGFloat theta = angle + (bottom ? delta : -delta);
                CGContextSaveGState(context);
                @try {
                    CGContextTranslateCTM(context, center.x + cos(theta) * radius, center.y + sin(theta) * radius);
                    CGContextRotateCTM(context, theta + (bottom ? M_PI / 2 : -M_PI / 2));
                    CGContextSetTextPosition(context, -advance / 2, -(_ascender - _descender) / 2);
                    AO_FAULT(ArcDraw);
                    CTLineDraw((__bridge CTLineRef)_characterLines[index], context);
                } @finally {
                    CGContextRestoreGState(context);
                }
                offset += width;
            }
            success = YES;
        }
    } @catch (NSException *exception) {
        AOFailure("arc_draw", exception, _fontSize, _characterCount);
    } @finally {
        CGContextRestoreGState(context);
        CGContextSetTextMatrix(context, originalTextMatrix);
        CGContextSetTextPosition(context, originalTextPosition.x, originalTextPosition.y);
        if (resolved != NULL) { CGColorRelease(resolved); }
    }
    if (!success) { AOPlaceholder(context, placeholder); }
    return success;
}
@end
