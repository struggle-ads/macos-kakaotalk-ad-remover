#import <Cocoa/Cocoa.h>
#import <objc/runtime.h>
#import <dlfcn.h>

static NSUInteger checks;
static void Check(BOOL condition, const char *message) {
    checks++;
    if (!condition) { fprintf(stderr, "FAIL: %s\n", message); exit(1); }
}

@interface FixtureController : NSViewController
@property(nonatomic, strong) NSView *advertiseContainerView;
@property(nonatomic, strong) NSView *adSeparator;
@property(nonatomic, strong) NSLayoutConstraint *advertiseContainerHeightConstraint;
@property(nonatomic, strong) NSLayoutConstraint *adSeparatorHeightConstraint;
@property(nonatomic) NSUInteger assignmentCount;
@end
@implementation FixtureController
- (void)setAdvertiseContainerView:(NSView *)view {
    _advertiseContainerView = view;
    self.assignmentCount++;
}
@end

static Class Register(NSString *name) {
    Class cls = objc_allocateClassPair(FixtureController.class, name.UTF8String, 0);
    Check(cls != Nil, "fixture class registration");
    objc_registerClassPair(cls);
    return cls;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        Check(argc == 2, "payload argument");
        [NSApplication sharedApplication];
        Class mainClass = Register(@"_TtC9KakaoTalk20FSMainViewController");
        NSWindow *window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 400, 600)
                     styleMask:NSWindowStyleMaskBorderless backing:NSBackingStoreBuffered defer:NO];
        NSView *root = window.contentView;
        NSView *ad = [[NSView alloc] init];
        NSView *separator = [[NSView alloc] init];
        NSView *content = [[NSView alloc] init];
        for (NSView *view in @[ad, separator, content]) {
            view.translatesAutoresizingMaskIntoConstraints = NO;
            [root addSubview:view];
            [NSLayoutConstraint activateConstraints:@[
                [view.leadingAnchor constraintEqualToAnchor:root.leadingAnchor],
                [view.trailingAnchor constraintEqualToAnchor:root.trailingAnchor]]];
        }
        NSLayoutConstraint *height = [ad.heightAnchor constraintEqualToConstant:100];
        NSLayoutConstraint *lineHeight = [separator.heightAnchor constraintEqualToConstant:1];
        [NSLayoutConstraint activateConstraints:@[
            height, lineHeight, [ad.bottomAnchor constraintEqualToAnchor:root.bottomAnchor],
            [separator.bottomAnchor constraintEqualToAnchor:ad.topAnchor],
            [content.bottomAnchor constraintEqualToAnchor:separator.topAnchor],
            [content.topAnchor constraintEqualToAnchor:root.topAnchor]]];
        [root layoutSubtreeIfNeeded];
        Check(ad.frame.size.height == 100 && content.frame.size.height == 499,
              "baseline has a real 101-point advertisement allocation");

        void *library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
        if (!library) fprintf(stderr, "%s\n", dlerror());
        Check(library != NULL, "load payload");
        FixtureController *controller = [[mainClass alloc] init];
        controller.advertiseContainerView = ad;
        controller.adSeparator = separator;
        controller.advertiseContainerHeightConstraint = height;
        controller.adSeparatorHeightConstraint = lineHeight;
        [root layoutSubtreeIfNeeded];
        Check(ad.hidden && separator.hidden, "only selected advertisement views are hidden");
        Check(height.constant == 0 && lineHeight.constant == 0, "ad constraints collapse synchronously");
        Check(content.frame.size.height == 600, "content reclaims all 101 points");
        Check(controller.assignmentCount == 1, "original outlet setter still runs exactly once");

        height.constant = 100;
        lineHeight.constant = 1;
        ad.hidden = NO;
        separator.hidden = NO;
        [root layoutSubtreeIfNeeded];
        Check(ad.hidden && separator.hidden && height.constant == 0 && lineHeight.constant == 0,
              "an attempted ad refresh cannot restore the reserved space");
        [window setContentSize:NSMakeSize(480, 720)];
        [root layoutSubtreeIfNeeded];
        Check(content.frame.size.height == 720, "resizing keeps the content expanded");

        FixtureController *unrelated = [[FixtureController alloc] init];
        NSView *otherView = [[NSView alloc] init];
        NSLayoutConstraint *otherHeight = [otherView.heightAnchor constraintEqualToConstant:42];
        unrelated.advertiseContainerView = otherView;
        unrelated.advertiseContainerHeightConstraint = otherHeight;
        otherView.hidden = NO;
        otherHeight.constant = 43;
        Check(!otherView.hidden && otherHeight.constant == 43,
              "parent-class setters and unrelated AppKit objects remain unaffected");

        NSView *replacement = [[NSView alloc] init];
        NSLayoutConstraint *replacementHeight = [replacement.heightAnchor constraintEqualToConstant:99];
        controller.advertiseContainerView = replacement;
        controller.advertiseContainerHeightConstraint = replacementHeight;
        ad.hidden = NO;
        height.constant = 30;
        Check(replacement.hidden && replacementHeight.constant == 0, "replacement outlets become protected");
        Check(!ad.hidden && height.constant == 30, "replaced objects can be reused normally");
        controller.advertiseContainerView = nil;
        controller.advertiseContainerHeightConstraint = nil;
        replacement.hidden = NO;
        replacementHeight.constant = 40;
        Check(!replacement.hidden && replacementHeight.constant == 40, "clearing outlets releases protection");

        NSView *orphanView = [[NSView alloc] init];
        NSLayoutConstraint *orphanHeight = [orphanView.heightAnchor constraintEqualToConstant:100];
        @autoreleasepool {
            FixtureController *temporary = [[mainClass alloc] init];
            temporary.advertiseContainerView = orphanView;
            temporary.advertiseContainerHeightConstraint = orphanHeight;
        }
        orphanView.hidden = NO;
        orphanHeight.constant = 55;
        Check(!orphanView.hidden && orphanHeight.constant == 55, "released controllers do not leave orphan pins");

        Class lockClass = Register(@"_TtC9KakaoTalk20FSLockViewController");
        [[NSNotificationCenter defaultCenter] postNotificationName:NSApplicationDidFinishLaunchingNotification object:NSApp];
        FixtureController *lock = [[lockClass alloc] init];
        lock.advertiseContainerView = [[NSView alloc] init];
        lock.advertiseContainerHeightConstraint = [lock.advertiseContainerView.heightAnchor constraintEqualToConstant:100];
        Check(lock.advertiseContainerView.hidden && lock.advertiseContainerHeightConstraint.constant == 0,
              "late controller classes install through a launch event without polling");
        [[NSNotificationCenter defaultCenter] postNotificationName:NSApplicationDidFinishLaunchingNotification object:NSApp];
        lock.advertiseContainerView = [[NSView alloc] init];
        Check(lock.assignmentCount == 2, "repeated events never stack duplicate hooks");
        printf("PASS: %lu native hook checks\n", (unsigned long)checks);
    }
    return 0;
}
