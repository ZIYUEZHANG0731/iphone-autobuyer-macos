"""Native ownership regression without any system accessibility permissions."""
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from app.platforms import accessibility


def test_non_macos_does_not_load_the_cocoa_workaround(monkeypatch):
    monkeypatch.setattr(accessibility.sys, 'platform', 'win32')
    monkeypatch.setattr(accessibility.ctypes, 'CDLL', lambda *_: pytest.fail('No native load'))
    assert accessibility.prepare_accessibility() is False


@pytest.mark.skipif(sys.platform != 'darwin', reason='Cocoa ownership fixture needs macOS')
@pytest.mark.parametrize('identifier_type', ['uint32_t', 'uint64_t'])
def test_synthesized_element_cleanup_preserves_parent_and_checks_abi(tmp_path, identifier_type):
    root = Path(__file__).resolve().parents[1]
    fixture = tmp_path / 'ownership.m'
    fixture.write_text(textwrap.dedent('''
        #import <Foundation/Foundation.h>
        #include <assert.h>
        #include <stdint.h>
        int ABInstallCocoaAccessibilityOwnershipFix(void);
        static int parentDeletes, cellDeletes;
        @interface QMacAccessibilityElement : NSObject {
        @public
            IDENTIFIER_TYPE axid;
            BOOL managed;
        }
        - (BOOL)isManagedByParent;
        + (void)removeElementsFromCache:(NSArray *)elements;
        @end
        @implementation QMacAccessibilityElement
        - (BOOL)isManagedByParent { return managed; }
        + (void)removeElementsFromCache:(NSArray *)elements {
            for (QMacAccessibilityElement *element in elements) {
                if (element->axid == 42) parentDeletes++;
                if (element->axid == 43) cellDeletes++;
            }
        }
        - (void)dealloc {
            if (axid == 42) parentDeletes++;
            if (axid == 43) cellDeletes++;
            [super dealloc];
        }
        @end
        int main(void) {
            @autoreleasepool {
                int installed = ABInstallCocoaAccessibilityOwnershipFix();
                if (sizeof(IDENTIFIER_TYPE) != 4) {
                    assert(installed == -1);
                    puts("UNSUPPORTED_ABI_REJECTED");
                    return 0;
                }
                assert(installed == 1);
                assert(ABInstallCocoaAccessibilityOwnershipFix() == 1);
                QMacAccessibilityElement *placeholder = [QMacAccessibilityElement new];
                placeholder->axid = 42; placeholder->managed = YES;
                QMacAccessibilityElement *cell = [QMacAccessibilityElement new];
                cell->axid = 43; cell->managed = NO;
                NSArray *children = [[NSArray alloc] initWithObjects:placeholder, cell, nil];
                @autoreleasepool {
                    [QMacAccessibilityElement removeElementsFromCache:children];
                }
                assert(parentDeletes == 0 && cellDeletes == 1);
                [children release];
                [placeholder release]; [cell release];
                assert(parentDeletes == 0 && cellDeletes == 2);
                puts("PARENT_INTERFACE_PRESERVED");
            }
            return 0;
        }
    ''').replace('IDENTIFIER_TYPE', identifier_type), encoding='utf-8')
    executable = tmp_path / 'ownership'
    source = root / 'app/platforms/native/cocoa_accessibility.m'
    compiled = subprocess.run(['xcrun', 'clang', '-O2', '-fno-objc-arc', '-framework', 'Foundation',
                               str(source), str(fixture), '-o', str(executable)],
                              capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = 'PARENT_INTERFACE_PRESERVED' if identifier_type == 'uint32_t' else 'UNSUPPORTED_ABI_REJECTED'
    assert expected in result.stdout
