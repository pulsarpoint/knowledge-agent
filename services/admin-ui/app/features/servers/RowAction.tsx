import type { ReactNode } from "react";
import { Link } from "react-router";
import { Button } from "~/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "~/components/ui/tooltip";
import { cn } from "~/lib/utils";

/** A small icon-only row button with a tooltip; a link when `to` is given. The label is the accessible name. */
export function RowAction({
  label,
  name,
  tip,
  to,
  disabled,
  destructive,
  onClick,
  children,
}: {
  label: string;
  name: string;
  tip?: string;
  to?: string;
  disabled?: boolean;
  destructive?: boolean;
  onClick?: () => void;
  children: ReactNode;
}) {
  const className = cn("h-8 w-8", destructive && "text-destructive hover:text-destructive");
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {/* the span keeps the tooltip working on a disabled button */}
        <span className="inline-flex">
          {to ? (
            <Button asChild variant="ghost" size="icon" className={className}>
              <Link to={to} aria-label={`${label} ${name}`}>
                {children}
              </Link>
            </Button>
          ) : (
            <Button
              variant="ghost"
              size="icon"
              className={className}
              aria-label={`${label} ${name}`}
              disabled={disabled}
              onClick={onClick}
            >
              {children}
            </Button>
          )}
        </span>
      </TooltipTrigger>
      <TooltipContent>{tip ?? label}</TooltipContent>
    </Tooltip>
  );
}

export function RowActions({ children }: { children: ReactNode }) {
  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex justify-end gap-0.5">{children}</div>
    </TooltipProvider>
  );
}
