#import <Cocoa/Cocoa.h>
#import <objc/message.h>
#import <objc/runtime.h>

// An event-driven local UI patch. No timer, network interception, or chat access.
@interface KAPAdPin : NSObject
@property(nonatomic, weak) id owner;
@property(nonatomic) SEL getter;
@end
@implementation KAPAdPin
@end

static char heightKey, visibilityKey, installedKey;
static IMP originalSetConstant, originalSetHidden;

static id ReadOutlet(id owner, SEL getter) {
    return ((id (*)(id, SEL))objc_msgSend)(owner, getter);
}

static BOOL IsPinned(id object, const void *key) {
    KAPAdPin *pin = objc_getAssociatedObject(object, key);
    id owner = pin.owner;
    return owner && ReadOutlet(owner, pin.getter) == object;
}

static void SetConstant(id self, SEL cmd, CGFloat value) {
    if (IsPinned(self, &heightKey)) value = 0;
    ((void (*)(id, SEL, CGFloat))originalSetConstant)(self, cmd, value);
}

static void SetHidden(id self, SEL cmd, BOOL value) {
    if (IsPinned(self, &visibilityKey)) value = YES;
    ((void (*)(id, SEL, BOOL))originalSetHidden)(self, cmd, value);
}

static void InstallValueHooks(void) {
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        originalSetConstant = method_setImplementation(
            class_getInstanceMethod(NSLayoutConstraint.class, @selector(setConstant:)), (IMP)SetConstant);
        originalSetHidden = method_setImplementation(
            class_getInstanceMethod(NSView.class, @selector(setHidden:)), (IMP)SetHidden);
    });
}

static BOOL IsObjectMethod(Method method, unsigned int arguments, char returnType) {
    if (!method || method_getNumberOfArguments(method) != arguments) return NO;
    char type[16] = {0};
    method_getReturnType(method, type, sizeof(type));
    if (type[0] != returnType) return NO;
    if (arguments == 3) {
        method_getArgumentType(method, 2, type, sizeof(type));
        if (type[0] != '@') return NO;
    }
    return YES;
}

static SEL SetterFor(NSString *name) {
    NSString *capital = [[[name substringToIndex:1] uppercaseString] stringByAppendingString:[name substringFromIndex:1]];
    return NSSelectorFromString([NSString stringWithFormat:@"set%@:", capital]);
}

static void InstallController(NSString *className, NSArray<NSString *> *outlets) {
    Class cls = NSClassFromString(className);
    if (!cls || objc_getAssociatedObject(cls, &installedKey)) return;
    // Validate the whole interface before changing any of its methods.
    for (NSString *name in outlets) {
        if (!IsObjectMethod(class_getInstanceMethod(cls, NSSelectorFromString(name)), 2, '@') ||
            !IsObjectMethod(class_getInstanceMethod(cls, SetterFor(name)), 3, 'v')) return;
    }
    InstallValueHooks();
    for (NSString *name in outlets) {
        SEL getter = NSSelectorFromString(name);
        SEL setter = SetterFor(name);
        Method method = class_getInstanceMethod(cls, setter);
        IMP original = method_getImplementation(method);
        BOOL isHeight = [name hasSuffix:@"HeightConstraint"];
        const void *key = isHeight ? (void *)&heightKey : (void *)&visibilityKey;
        IMP replacement = imp_implementationWithBlock(^(id owner, id value) {
            id previous = ReadOutlet(owner, getter);
            ((void (*)(id, SEL, id))original)(owner, setter, value);
            if (previous && previous != value) {
                KAPAdPin *oldPin = objc_getAssociatedObject(previous, key);
                if (oldPin.owner == owner && oldPin.getter == getter)
                    objc_setAssociatedObject(previous, key, nil, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
            }
            Class expected = isHeight ? NSLayoutConstraint.class : NSView.class;
            if (![value isKindOfClass:expected]) return;
            KAPAdPin *pin = [[KAPAdPin alloc] init];
            pin.owner = owner;
            pin.getter = getter;
            objc_setAssociatedObject(value, key, pin, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
            if (isHeight) [(NSLayoutConstraint *)value setConstant:0];
            else [(NSView *)value setHidden:YES];
        });
        // Unlike method_setImplementation on an inherited Method, this does not
        // replace the superclass setter used by unrelated view controllers.
        class_replaceMethod(cls, setter, replacement, method_getTypeEncoding(method));
    }
    objc_setAssociatedObject(cls, &installedKey, @YES, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
}

static void Install(void) {
    InstallController(@"_TtC9KakaoTalk20FSMainViewController", @[
        @"advertiseContainerView", @"advertiseContainerHeightConstraint",
        @"adSeparator", @"adSeparatorHeightConstraint"]);
    InstallController(@"_TtC9KakaoTalk20FSLockViewController", @[
        @"advertiseContainerView", @"advertiseContainerHeightConstraint"]);
}

__attribute__((constructor)) static void Initialize(void) {
    @autoreleasepool {
        Install();
        // One lifecycle fallback for classes that are not registered yet.
        [[NSNotificationCenter defaultCenter] addObserverForName:NSApplicationDidFinishLaunchingNotification
            object:nil queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *notification) { Install(); }];
    }
}
