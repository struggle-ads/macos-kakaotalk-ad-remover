#import <Cocoa/Cocoa.h>

static void KeepAlive(void *context) {}

static BOOL WaitForCompletion(BOOL (^done)(void), NSTimeInterval timeout) {
    if (done()) return YES;
    // Keep the run loop alive while KVO/launch completion waits for an event.
    // There is no periodic timer or forced termination.
    CFRunLoopSourceContext context = {0};
    context.perform = KeepAlive;
    CFRunLoopSourceRef source = CFRunLoopSourceCreate(NULL, 0, &context);
    CFRunLoopAddSource(CFRunLoopGetMain(), source, kCFRunLoopDefaultMode);
    CFRunLoopRunInMode(kCFRunLoopDefaultMode, timeout, false);
    CFRunLoopRemoveSource(CFRunLoopGetMain(), source, kCFRunLoopDefaultMode);
    CFRelease(source);
    return done();
}

@interface TerminationObserver : NSObject
@property(nonatomic, strong) NSArray<NSRunningApplication *> *applications;
@property(nonatomic) BOOL done;
- (void)refresh;
@end
@implementation TerminationObserver
- (void)refresh {
    for (NSRunningApplication *app in self.applications) if (!app.terminated) return;
    self.done = YES;
    CFRunLoopStop(CFRunLoopGetMain());
}
- (void)observeValueForKeyPath:(NSString *)keyPath ofObject:(id)object change:(NSDictionary *)change context:(void *)context {
    if (NSThread.isMainThread) [self refresh];
    else dispatch_async(dispatch_get_main_queue(), ^{ [self refresh]; });
}
@end

static void PrintJSON(NSDictionary *value) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value options:0 error:nil];
    puts([[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding].UTF8String);
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 3 || (strcmp(argv[1], "stop") && strcmp(argv[1], "open"))) {
            fputs("Usage: KakaoAppControl stop|open /path/KakaoTalk.app\n", stderr);
            return 2;
        }
        NSString *path = [NSString stringWithUTF8String:argv[2]];
        if (!path.isAbsolutePath || ![path.pathExtension isEqualToString:@"app"]) {
            fputs("절대 경로의 .app 폴더가 필요합니다.\n", stderr);
            return 2;
        }
        NSURL *url = [[NSURL fileURLWithPath:path isDirectory:YES] URLByResolvingSymlinksInPath];
        if (!strcmp(argv[1], "open")) {
            __block BOOL done = NO;
            __block NSError *failure;
            __block pid_t launchedPID = 0;
            [[NSWorkspace sharedWorkspace] openApplicationAtURL:url configuration:NSWorkspaceOpenConfiguration.configuration
                completionHandler:^(NSRunningApplication *app, NSError *error) {
                    dispatch_async(dispatch_get_main_queue(), ^{
                        failure = error;
                        launchedPID = app.processIdentifier;
                        done = YES;
                        CFRunLoopStop(CFRunLoopGetMain());
                    });
                }];
            if (!WaitForCompletion(^BOOL { return done; }, 15) || failure) {
                fprintf(stderr, "자동 실행 실패: %s\n", failure ? failure.localizedDescription.UTF8String : "timeout");
                return 3;
            }
            PrintJSON(@{@"opened":@YES, @"pid":@(launchedPID)});
            return 0;
        }

        NSString *expected = [[url URLByAppendingPathComponent:@"Contents/MacOS/KakaoTalk"] path];
        NSMutableArray<NSRunningApplication *> *matches = [NSMutableArray array];
        for (NSRunningApplication *app in NSWorkspace.sharedWorkspace.runningApplications) {
            NSString *candidate = app.executableURL.URLByResolvingSymlinksInPath.path;
            if ([candidate isEqualToString:expected] && !app.terminated) [matches addObject:app];
        }
        TerminationObserver *observer = [[TerminationObserver alloc] init];
        observer.applications = matches;
        for (NSRunningApplication *app in matches)
            [app addObserver:observer forKeyPath:@"terminated" options:0 context:NULL];
        for (NSRunningApplication *app in matches)
            [app terminate];
        [observer refresh];
        // The observed exit is authoritative; terminate can return NO if the
        // process exited just before the request was sent.
        WaitForCompletion(^BOOL { return observer.done; }, 15);
        [observer refresh];
        for (NSRunningApplication *app in matches)
            [app removeObserver:observer forKeyPath:@"terminated"];
        if (!observer.done) {
            fputs("카카오톡 정상 종료가 거부되었거나 완료되지 않았습니다. 강제 종료하지 않습니다.\n", stderr);
            return 3;
        }
        PrintJSON(@{@"was_running":matches.count ? @YES : @NO, @"stopped":@(matches.count)});
        return 0;
    }
}
