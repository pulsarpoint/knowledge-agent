import { NavLink, useLocation, useResolvedPath, type To } from "react-router";
import { cn } from "~/lib/utils";

export interface Section {
  to: To;
  label: string;
  /** The link is open only on its exact path. */
  end?: boolean;
  /** What the sub-page is for: one line shown under the links while it is open. */
  help?: string;
}

/** A path without its trailing slash, decoded: a link built with encodeURIComponent (`cc%3As1`) and the path the
 * router resolves (`cc:s1`) compare equal, as NavLink compares them. */
function trimmed(path: string) {
  let decoded = path;
  try {
    decoded = decodeURIComponent(path);
  } catch {
    /* a malformed escape: compare the path as it is */
  }
  return decoded.length > 1 ? decoded.replace(/\/$/, "") : decoded;
}

/** A section's help line, shown while the section is open (open as its NavLink is). */
function SectionHelp({ section }: { section: Section }) {
  const { pathname } = useLocation();
  const path = trimmed(useResolvedPath(section.to).pathname);
  const here = trimmed(pathname);
  const open = here === path || (!section.end && here.startsWith(path) && here.charAt(path.length) === "/");
  return open ? <p className="text-sm text-muted-foreground">{section.help}</p> : null;
}

/** A page's sub-pages as tab-styled links (the open one marked), then the open sub-page's help line. */
export default function SectionLinks({ label, sections }: { label: string; sections: Section[] }) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
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
      {sections.map((section) => section.help && <SectionHelp key={section.label} section={section} />)}
    </div>
  );
}
