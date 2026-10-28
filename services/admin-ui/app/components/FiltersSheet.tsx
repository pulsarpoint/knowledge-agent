import { useState, type ReactNode } from "react";
import { SlidersHorizontal } from "lucide-react";
import { badgeVariants } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "~/components/ui/sheet";

/** A list's less used filters (past the three shown in the toolbar row): a Filters button with a count badge while any
 * of them is set, opening a right Sheet with them and Reset. The filters keep their state where the page keeps it (the
 * URL or state); `active` counts the set ones and `onReset` clears them. */
export default function FiltersSheet({
  active,
  onReset,
  children,
}: {
  active: number;
  onReset: () => void;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        variant="outline"
        aria-label={active ? `Filters, ${active} active` : "Filters"}
        onClick={() => setOpen(true)}
      >
        <SlidersHorizontal data-icon="inline-start" />
        Filters
        {active > 0 && <span className={badgeVariants({ variant: "secondary" })}>{active}</span>}
      </Button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-sm">
          <SheetHeader>
            <SheetTitle>Filters</SheetTitle>
            <SheetDescription>More filters for this list.</SheetDescription>
          </SheetHeader>
          {/* Full width here: the toolbar's fixed filter widths do not apply in the Sheet. */}
          <div className="flex flex-col gap-3 [&_[role=combobox]]:w-full [&_input:not([type=checkbox])]:w-full">
            {children}
          </div>
          <SheetFooter>
            <Button variant="outline" disabled={active === 0} onClick={onReset}>
              Reset
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </>
  );
}
