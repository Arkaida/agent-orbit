#import <AppKit/AppKit.h>

NS_ASSUME_NONNULL_BEGIN

/// Immutable CoreText layout. A failed layout remains drawable as a geometric
/// placeholder; no Objective-C text exception is allowed to unwind into Swift.
@interface AOTextLayout : NSObject
@property(nonatomic, readonly) CGFloat width;
@property(nonatomic, readonly) CGFloat arcWidth;
@property(nonatomic, readonly) CGFloat height;
@property(nonatomic, readonly) CGFloat ascender;
@property(nonatomic, readonly) NSUInteger characterCount;
@property(nonatomic, readonly) BOOL isUsable;

+ (instancetype)layoutWithText:(NSString *)text fontSize:(CGFloat)fontSize
    NS_SWIFT_NAME(make(text:fontSize:));
- (BOOL)drawArcInContext:(CGContextRef)context center:(CGPoint)center
                 radius:(CGFloat)radius angle:(CGFloat)angle color:(NSColor *)color
    NS_SWIFT_NAME(drawArc(in:center:radius:angle:color:));
- (BOOL)drawInContext:(CGContextRef)context rect:(CGRect)rect
               color:(NSColor *)color centered:(BOOL)centered
    NS_SWIFT_NAME(draw(in:rect:color:centered:));
@end

#if defined(ORBIT_TEXT_TESTING)
/// Present only in dedicated regression builds; never linked into the app.
typedef NS_ENUM(NSInteger, AOTextTestFault) {
    AOTextTestFaultNone = 0,
    AOTextTestFaultLayout = 1,
    AOTextTestFaultArcDraw = 2,
    AOTextTestFaultFlatDraw = 3,
    AOTextTestFaultColor = 4,
};
FOUNDATION_EXPORT void AOTextTestInjectFault(AOTextTestFault fault);
FOUNDATION_EXPORT NSUInteger AOTextTestDiagnosticCount(void);
FOUNDATION_EXPORT NSUInteger AOTextTestPlaceholderCount(void);
#endif

NS_ASSUME_NONNULL_END
