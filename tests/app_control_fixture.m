#import <Cocoa/Cocoa.h>

@interface FixtureDelegate : NSObject <NSApplicationDelegate>
@property(nonatomic) BOOL refuseQuit;
@end
@implementation FixtureDelegate
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    puts("READY");
    fflush(stdout);
}
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
    return self.refuseQuit ? NSTerminateCancel : NSTerminateNow;
}
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyProhibited];
        __attribute__((objc_precise_lifetime)) FixtureDelegate *delegate = [[FixtureDelegate alloc] init];
        delegate.refuseQuit = argc > 1 && !strcmp(argv[1], "--refuse");
        NSApp.delegate = delegate;
        [NSApp run];
    }
    return 0;
}
