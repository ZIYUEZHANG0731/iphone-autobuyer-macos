// Qt 6.11.2 Cocoa ownership workaround, scoped to this application's runtime.
// Synthesized rows/columns/cell placeholders share their parent's interface ID.
// They must not destroy it when Qt replaces a placeholder with a real cell.
// Upstream: QTBUG-149612 / qtbase change 765434.
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <stdint.h>
#include <string.h>

static IMP originalCleanup;
static IMP originalDealloc;
static SEL managedSelector;
static ptrdiff_t interfaceOffset;

static BOOL parentManaged(id element)
{
    IMP implementation = [element methodForSelector:managedSelector];
    return ((BOOL (*)(id, SEL))implementation)(element, managedSelector);
}

static void cleanupOwnedInterfaces(id classObject, SEL selector, NSArray *elements)
{
    NSMutableArray *owned = [NSMutableArray arrayWithCapacity:elements.count];
    for (id element in elements) {
        if (!parentManaged(element))
            [owned addObject:element];
    }
    ((void (*)(id, SEL, NSArray *))originalCleanup)(classObject, selector, owned);
}

static void deallocateElement(id element, SEL selector)
{
    if (parentManaged(element)) {
        // Zero is not a registered QAccessible ID. The original destructor
        // still releases children and the Objective-C object normally.
        uint32_t noInterface = 0;
        memcpy((char *)element + interfaceOffset, &noInterface, sizeof(noInterface));
    }
    ((void (*)(id, SEL))originalDealloc)(element, selector);
}

int ABInstallCocoaAccessibilityOwnershipFix(void)
{
    static int installed;
    if (installed)
        return 1;
    Class element = objc_getClass("QMacAccessibilityElement");
    if (!element)
        return 0;
    managedSelector = sel_registerName("isManagedByParent");
    Method managed = class_getInstanceMethod(element, managedSelector);
    Method cleanup = class_getClassMethod(element, sel_registerName("removeElementsFromCache:"));
    Method destructor = class_getInstanceMethod(element, sel_registerName("dealloc"));
    Ivar interfaceID = class_getInstanceVariable(element, "axid");
    // Refuse to mutate a different Qt ABI. Python also requires Qt 6.11.2.
    if (!managed || !cleanup || !destructor || !interfaceID
        || strcmp(ivar_getTypeEncoding(interfaceID), @encode(uint32_t))
        || method_getNumberOfArguments(cleanup) != 3
        || method_getNumberOfArguments(destructor) != 2)
        return -1;
    interfaceOffset = ivar_getOffset(interfaceID);
    originalCleanup = method_setImplementation(cleanup, (IMP)cleanupOwnedInterfaces);
    originalDealloc = method_setImplementation(destructor, (IMP)deallocateElement);
    installed = 1;
    return 1;
}
