import { useId, useRef, type ReactNode } from "react";
import { Link } from "react-router";
import { MoreHorizontal, Trash2, type LucideIcon } from "lucide-react";
import { Button } from "~/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "~/components/ui/dropdown-menu";

/** One item of a row menu: a link (`to`), an action (`onSelect`) or a file picker (`accept` + `onFile`; one per menu).
 * A `destructive` item (Delete, Remove, Discard) opens the page's confirmation. A disabled item shows its `hint` (why
 * it is off) under the label. */
export type RowMenuItem = { label: string; icon: LucideIcon; disabled?: boolean; hint?: string } & (
  { to: string } | { onSelect: () => void; destructive?: boolean } | { accept: string; onFile: (file: File) => void }
);

/** The usual last item: Delete (or Remove / Discard) with a bin; `onSelect` opens `ConfirmRemove`. */
export const deleteMenuItem = (
  onSelect: () => void,
  label: "Delete" | "Remove" | "Discard" = "Delete",
): RowMenuItem => ({
  label,
  icon: Trash2,
  onSelect,
  destructive: true,
});

const isDestructive = (item: RowMenuItem) => "onSelect" in item && !!item.destructive;

/** A row's actions in one "⋯" menu named `Actions for <name>`: the items as icon + label, the destructive ones last,
 * after a separator, in destructive text. Falsy items are skipped, so a page can write `condition && { … }`. */
export default function RowMenu({
  name,
  items,
  disabled = false,
}: {
  name: string;
  items: (RowMenuItem | false | null | undefined)[];
  disabled?: boolean;
}) {
  const file = useRef<HTMLInputElement>(null);
  const present = items.filter((item): item is RowMenuItem => !!item);
  const plain = present.filter((item) => !isDestructive(item));
  const destructive = present.filter(isDestructive);
  const picker = present.find((item) => "onFile" in item);
  const ids = useId();
  if (present.length === 0) return null;
  /** The item's icon and label, with its hint under the label when it is disabled (its description). */
  const content = (item: RowMenuItem, key: string): { body: ReactNode; describedBy?: string } => {
    const Icon = item.icon;
    const hint = item.disabled && item.hint ? `${ids}-${key}` : undefined;
    return {
      describedBy: hint,
      body: (
        <>
          <Icon />
          <span className="flex flex-col">
            <span>{item.label}</span>
            {hint && (
              <span id={hint} className="text-xs text-muted-foreground">
                {item.hint}
              </span>
            )}
          </span>
        </>
      ),
    };
  };
  return (
    <>
      {picker && "onFile" in picker && (
        // Outside the menu: the menu content unmounts when it closes, before the file is chosen.
        <input
          ref={file}
          type="file"
          accept={picker.accept}
          className="sr-only"
          tabIndex={-1}
          aria-label={`File for ${name}`}
          disabled={disabled || picker.disabled}
          onChange={(event) => {
            const chosen = event.target.files?.[0];
            event.target.value = "";
            if (chosen) picker.onFile(chosen);
          }}
        />
      )}
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" aria-label={`Actions for ${name}`} disabled={disabled}>
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {plain.map((item, index) => {
            const { body, describedBy } = content(item, `p${index}`);
            if ("to" in item)
              return (
                <DropdownMenuItem key={`${item.label}-${index}`} asChild disabled={item.disabled}>
                  <Link to={item.to} aria-label={item.label} aria-describedby={describedBy}>
                    {body}
                  </Link>
                </DropdownMenuItem>
              );
            return (
              <DropdownMenuItem
                key={`${item.label}-${index}`}
                aria-label={item.label}
                aria-describedby={describedBy}
                disabled={item.disabled}
                onSelect={"onSelect" in item ? item.onSelect : () => file.current?.click()}
              >
                {body}
              </DropdownMenuItem>
            );
          })}
          {plain.length > 0 && destructive.length > 0 && <DropdownMenuSeparator />}
          {destructive.map((item, index) => {
            const { body, describedBy } = content(item, `d${index}`);
            return (
              <DropdownMenuItem
                key={`${item.label}-${index}`}
                aria-label={item.label}
                aria-describedby={describedBy}
                className="text-destructive focus:text-destructive"
                disabled={item.disabled}
                onSelect={"onSelect" in item ? item.onSelect : undefined}
              >
                {body}
              </DropdownMenuItem>
            );
          })}
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  );
}
