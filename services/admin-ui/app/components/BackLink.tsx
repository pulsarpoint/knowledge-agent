import { Link, type To } from "react-router";
import { ArrowLeft } from "lucide-react";
import { Button } from "~/components/ui/button";

/** The way back from a record to its list, first in the record's header: "All <plural>". */
export default function BackLink({ to, children }: { to: To; children: React.ReactNode }) {
  return (
    <Button asChild variant="link" className="self-start px-0">
      <Link to={to}>
        <ArrowLeft data-icon="inline-start" />
        {children}
      </Link>
    </Button>
  );
}
