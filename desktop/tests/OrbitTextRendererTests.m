#import "../OrbitTextRenderer.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if !defined(ORBIT_TEXT_TESTING)
#error Regression fault injection must only be compiled in the dedicated test build.
#endif

static NSUInteger assertions;
static void check(BOOL condition, const char *message) {
    assertions += 1;
    if (!condition) { fprintf(stderr, "FAIL: %s\n", message); exit(1); }
}

static void unchanged(CGContextRef context, CGAffineTransform ctm, CGAffineTransform textMatrix, CGPoint textPosition) {
    check(CGAffineTransformEqualToTransform(CGContextGetCTM(context), ctm), "context transform restored");
    check(CGAffineTransformEqualToTransform(CGContextGetTextMatrix(context), textMatrix), "text matrix restored");
    check(CGPointEqualToPoint(CGContextGetTextPosition(context), textPosition), "text position restored");
}

static void clearPixels(CGContextRef context) {
    CGContextFlush(context);
    memset(CGBitmapContextGetData(context), 0, CGBitmapContextGetBytesPerRow(context) * CGBitmapContextGetHeight(context));
}

static BOOL hasInk(CGContextRef context) {
    CGContextFlush(context);
    const unsigned char *bytes = CGBitmapContextGetData(context);
    NSUInteger count = CGBitmapContextGetBytesPerRow(context) * CGBitmapContextGetHeight(context);
    for (NSUInteger index = 0; index < count; index += 1) { if (bytes[index] != 0) { return YES; } }
    return NO;
}

int main(void) {
    @autoreleasepool {
        CGColorSpaceRef space = CGColorSpaceCreateDeviceRGB();
        CGContextRef context = CGBitmapContextCreate(NULL, 400, 400, 8, 0, space, kCGImageAlphaPremultipliedLast);
        CGColorSpaceRelease(space);
        check(context != NULL, "bitmap context created");
        CGContextTranslateCTM(context, 3, 5);
        CGContextSetTextMatrix(context, CGAffineTransformMakeScale(0.9, 1.1));
        CGContextSetTextPosition(context, 7, 11);
        CGAffineTransform ctm = CGContextGetCTM(context), matrix = CGContextGetTextMatrix(context);
        CGPoint position = CGContextGetTextPosition(context);
        NSArray<NSString *> *examples = @[@"atlas", @"中文会话", @"👩🏽‍💻🇨🇳", @"e\u0301", @"a\nb\tc", @"", @"𝕏→مرحبا"];
        for (NSString *text in examples) {
            AOTextLayout *layout = [AOTextLayout layoutWithText:text fontSize:9.4];
            check(layout.isUsable, "synthetic Unicode layout usable");
            check(isfinite(layout.width) && layout.width >= 0, "finite flat width");
            check(isfinite(layout.arcWidth) && layout.arcWidth >= 0, "finite arc width");
            check(isfinite(layout.height) && layout.height > 0, "finite positive height");
            check([layout drawInContext:context rect:CGRectMake(40, 40, 120, 20) color:NSColor.cyanColor centered:YES], "flat draw succeeds");
            unchanged(context, ctm, matrix, position);
            check([layout drawArcInContext:context center:CGPointMake(150, 150) radius:70 angle:1.2 color:NSColor.cyanColor], "arc draw succeeds");
            unchanged(context, ctm, matrix, position);
        }
        NSUInteger diagnostics = AOTextTestDiagnosticCount(), placeholders = AOTextTestPlaceholderCount();
        AOTextTestInjectFault(AOTextTestFaultLayout);
        AOTextLayout *failed = [AOTextLayout layoutWithText:@"synthetic" fontSize:9.4];
        check(!failed.isUsable, "layout exception captured");
        check(isfinite(failed.width) && failed.width > 0, "failed layout has safe width");
        clearPixels(context);
        check(![failed drawInContext:context rect:CGRectMake(40, 40, 120, 20) color:NSColor.cyanColor centered:YES], "failed layout returns failure");
        check(AOTextTestDiagnosticCount() == diagnostics + 1, "layout exception records bounded metadata");
        check(AOTextTestPlaceholderCount() == placeholders + 1, "layout exception draws geometric placeholder");
        check(hasInk(context), "layout placeholder produces visible pixels");
        unchanged(context, ctm, matrix, position);
        AOTextLayout *layout = [AOTextLayout layoutWithText:@"synthetic" fontSize:9.4];
        for (NSNumber *fault in @[@(AOTextTestFaultArcDraw), @(AOTextTestFaultFlatDraw), @(AOTextTestFaultColor)]) {
            diagnostics = AOTextTestDiagnosticCount(); placeholders = AOTextTestPlaceholderCount();
            AOTextTestInjectFault(fault.integerValue);
            clearPixels(context);
            BOOL success = fault.integerValue == AOTextTestFaultArcDraw ?
                [layout drawArcInContext:context center:CGPointMake(150, 150) radius:70 angle:1.2 color:NSColor.cyanColor] :
                [layout drawInContext:context rect:CGRectMake(40, 40, 120, 20) color:NSColor.cyanColor centered:YES];
            check(!success, "draw exception captured and reported as failure");
            check(AOTextTestDiagnosticCount() == diagnostics + 1, "draw exception records bounded metadata");
            check(AOTextTestPlaceholderCount() == placeholders + 1, "draw exception draws geometric placeholder");
            check(hasInk(context), "draw placeholder produces visible pixels");
            unchanged(context, ctm, matrix, position);
        }
        for (NSNumber *size in @[@(NAN), @(INFINITY), @(-1), @(0), @(300)]) {
            check(![AOTextLayout layoutWithText:@"synthetic" fontSize:size.doubleValue].isUsable, "invalid font size rejected");
        }
        NSString *oversized = [@"a" stringByPaddingToLength:513 withString:@"a" startingAtIndex:0];
        check(![AOTextLayout layoutWithText:oversized fontSize:9.4].isUsable, "oversized input bounded");
        check(![layout drawInContext:context rect:CGRectMake(NAN, 0, 10, 10) color:NSColor.cyanColor centered:YES], "invalid flat geometry rejected");
        check(![layout drawArcInContext:context center:CGPointZero radius:0 angle:1 color:NSColor.cyanColor], "zero arc radius rejected");
        unchanged(context, ctm, matrix, position);
        check([layout drawArcInContext:context center:CGPointMake(150, 150) radius:70 angle:1.2 color:NSColor.cyanColor], "renderer recovers after injected faults");
        unchanged(context, ctm, matrix, position);
        CGContextRelease(context);
        printf("Orbit text renderer: %lu assertions passed\n", (unsigned long)assertions);
    }
    return 0;
}
