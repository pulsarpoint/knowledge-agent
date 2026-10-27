import { useRef } from "react";
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

/** One item of a row menu: a link (`to`), an action (`onSelect`) or a file picker (`accept` + `onFile`). A
 * `destructive` item (Delete, Remove, Discard) opens the page's confirmation. */
export type RowMenuItem = { label: string; icon: LucideIcon; disabled?: boolean } & (
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
          {plain.map((item) => {
            const Icon = item.icon;
            if ("to" in item)
              return (
                <DropdownMenuItem key={item.label} asChild disabled={item.disabled}>
                  <Link to={item.to}>
                    <Icon />
                    {item.label}
                  </Link>
                </DropdownMenuItem>
              );
            return (
              <DropdownMenuItem
                key={item.label}
                disabled={item.disabled}
                onSelect={"onSelect" in item ? item.onSelect : () => file.current?.click()}
              >
                <Icon />
                {item.label}
              </DropdownMenuItem>
            );
          })}
          {plain.length > 0 && destructive.length > 0 && <DropdownMenuSeparator />}
          {destructive.map((item) => {
            const Icon = item.icon;
            return (
              <DropdownMenuItem
                key={item.label}
                className="text-destructive focus:text-destructive"
                disabled={item.disabled}
                onSelect={"onSelect" in item ? item.onSelect : undefined}
              >
                <Icon />
                {item.label}
              </DropdownMenuItem>
            );
          })}
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  );
}
