import { NavLink, type To } from "react-router";
import { cn } from "~/lib/utils";

/** A page's sub-pages as tab-styled links (the open one marked). `end`: the link is open only on its exact path. */
export default function SectionLinks({
  label,
  sections,
}: {
  label: string;
  sections: { to: To; label: string; end?: boolean }[];
}) {
  return (
    <nav aria-label={label} className="overflow-x-auto">
      <div className="inline-flex h-10 w-max items-center rounded-md bg-muted p-1 text-muted-foreground">
        {sections.map((section) => (
          <NavLink
            key={section.label}
            to={section.to}
            end={section.end}
            className={({ isActive }) =>
              cn(
                "inline-flex items-center justify-center whitespace-nowrap rounded-sm px-3 py-1.5 text-sm font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                isActive && "bg-background text-foreground shadow-sm",
              )
            }
          >
            {section.label}
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
