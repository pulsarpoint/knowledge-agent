import { useEffect, useMemo } from "react";
import { hotkeysCoreFeature, syncDataLoaderFeature } from "@headless-tree/core";
import { useTree } from "@headless-tree/react";
import { ChevronRight, FileText, Folder, FolderOpen } from "lucide-react";
import { Button } from "~/components/ui/button";
import { cn } from "~/lib/utils";

export interface FileTreeEntry {
  path: string;
  kind: "folder" | "file";
  detail?: string;
}

interface FileTreeProps {
  entries: FileTreeEntry[];
  selectedPath?: string;
  onSelect: (entry: FileTreeEntry) => void;
  label: string;
  disabled?: boolean;
}

export default function FileTree({ entries, selectedPath, onSelect, label, disabled = false }: FileTreeProps) {
  const nodes = useMemo(() => {
    const result = new Map<string, FileTreeEntry & { children: string[] }>();
    result.set("", { path: "", kind: "folder", children: [] });
    for (const entry of entries) {
      const parts = entry.path.split("/");
      for (let depth = 1; depth < parts.length; depth++) {
        const path = parts.slice(0, depth).join("/");
        if (!result.has(path)) result.set(path, { path, kind: "folder", children: [] });
      }
      result.set(entry.path, { ...entry, children: [] });
    }
    for (const [path] of result) {
      if (path) result.get(path.split("/").slice(0, -1).join("/"))!.children.push(path);
    }
    for (const node of result.values()) {
      node.children.sort((a, b) => Number(result.get(b)!.kind === "folder") - Number(result.get(a)!.kind === "folder")
        || a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" }));
    }
    return result;
  }, [entries]);

  const tree = useTree<FileTreeEntry>({
    rootItemId: "",
    getItemName: (item) => item.getItemData().path.split("/").at(-1)!,
    isItemFolder: (item) => item.getItemData().kind === "folder",
    dataLoader: {
      getItem: (path) => nodes.get(path)!,
      getChildren: (path) => nodes.get(path)?.children ?? [],
    },
    onPrimaryAction: (item) => { if (!disabled) onSelect(item.getItemData()); },
    features: [syncDataLoaderFeature, hotkeysCoreFeature],
  });

  useEffect(() => {
    // Refresh the contents without resetting folders the user has expanded.
    tree.applySubStateUpdate("expandedItems", (paths) => paths.filter((path) => nodes.has(path)));
    const focused = tree.getState().focusedItem;
    if (focused && !nodes.has(focused)) tree.applySubStateUpdate("focusedItem", null);
    tree.rebuildTree();
  }, [nodes, tree]);

  useEffect(() => {
    // Reveal a deep link or newly selected file/folder, but allow manual collapse afterwards.
    if (!selectedPath) return;
    const parts = selectedPath.split("/");
    for (let depth = 1; depth < parts.length; depth++) {
      const item = tree.getItemInstance(parts.slice(0, depth).join("/"));
      if (!item.isExpanded()) item.expand();
    }
  }, [selectedPath, tree]);

  return <div {...tree.getContainerProps(label)} aria-disabled={disabled}
    className="flex max-h-[650px] min-w-0 flex-col gap-0.5 overflow-auto p-2"
    onKeyDownCapture={(event) => { if (disabled) { event.preventDefault(); event.stopPropagation(); } }}>
    {tree.getItems().filter((item) => nodes.has(item.getId())).map((item) => {
      const entry = item.getItemData();
      const Icon = item.isFolder() ? (item.isExpanded() ? FolderOpen : Folder) : FileText;
      return <div {...item.getProps()} key={item.getId()} aria-selected={entry.path === selectedPath}
        aria-disabled={disabled} title={entry.path}
        className={cn("flex min-h-10 cursor-pointer items-center gap-2 rounded-md py-1 pr-2 text-sm outline-none hover:bg-accent hover:text-accent-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
          entry.path === selectedPath && "bg-secondary text-secondary-foreground", disabled && "pointer-events-none opacity-50")}
        style={{ paddingLeft: 4 + item.getItemMeta().level * 16 }}
        onFocus={() => item.setFocused()}
        onClick={() => { if (!disabled) { item.setFocused(); item.primaryAction(); } }}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault(); event.stopPropagation(); item.primaryAction();
          }
        }}>
        {item.isFolder() ? <Button type="button" variant="ghost" size="icon" className="size-6 shrink-0" tabIndex={-1}
          disabled={disabled} aria-label={`${item.isExpanded() ? "Collapse" : "Expand"} ${entry.path}`}
          onClick={(event) => {
            event.stopPropagation(); item.setFocused(); item.getElement()?.focus();
            if (item.isExpanded()) item.collapse(); else item.expand();
          }}>
          <ChevronRight data-icon="inline-start" className={cn("transition-transform", item.isExpanded() && "rotate-90")} />
        </Button> : <span className="size-6 shrink-0" aria-hidden="true" />}
        <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className="min-w-0 flex-1">
          <span className="block truncate">{item.getItemName()}</span>
          {entry.detail && <span className="block truncate text-xs text-muted-foreground">{entry.detail}</span>}
        </span>
      </div>;
    })}
  </div>;
}
