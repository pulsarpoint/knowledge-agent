import { Link } from "react-router";
import { tagFilterPath } from "./api";
import { badgeVariants } from "~/components/ui/badge";
import { cn } from "~/lib/utils";

/** A document's tags as badges, each a link to the Documents list filtered by that tag. */
export default function TagLinks({ tags, label, className }: { tags: string[]; label?: string; className?: string }) {
  if (tags.length === 0) return null;
  return (
    <span
      role={label ? "group" : undefined}
      aria-label={label}
      className={cn("inline-flex flex-wrap items-center gap-1", className)}
    >
      {tags.map((tag) => (
        <Link
          key={tag}
          to={tagFilterPath(tag)}
          className={cn(badgeVariants({ variant: "outline" }), "text-xs font-normal hover:bg-accent")}
        >
          {tag}
        </Link>
      ))}
    </span>
  );
}
