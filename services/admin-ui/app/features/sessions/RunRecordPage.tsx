import { Outlet, useParams, useSearchParams } from "react-router";
import BackLink from "~/components/BackLink";
import SectionLinks from "~/components/SectionLinks";
import RunRecordView from "./RunRecordView";
import RunDebugView from "./RunDebugView";

const SECTIONS = [
  {
    page: ".",
    label: "Record",
    end: true,
    help: "Every event of the run in order: instructions, input, reasoning, messages, tool calls with their outputs, usage and errors.",
  },
  {
    page: "debug",
    label: "Debug",
    help: "Query the run's whole session with the session explorer: the methods a reviewer model calls as tools, with each result's size.",
  },
];

/** One run record (`/activity/runs/:runKey`): the way back, the sub-pages Record and Debug as links and the open one.
 * Record keeps an expert call's `?call=`; Debug explores the run's whole session (every turn), this run's turn preselected. */
export default function RunRecordLayout() {
  const call = useSearchParams()[0].get("call");
  const search = call ? `?${new URLSearchParams({ call })}` : "";
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <BackLink to="/activity/runs">All agent runs</BackLink>
        <SectionLinks
          label="Run record sections"
          sections={SECTIONS.map(({ page, ...section }) => ({
            ...section,
            to: { pathname: page, search: page === "." ? search : "" },
          }))}
        />
      </div>
      <Outlet />
    </div>
  );
}

/** Record: every event of the run (today's run page). */
export function RunRecordIndex() {
  const { runKey } = useParams() as { runKey: string };
  return <RunRecordView key={runKey} runKey={runKey} />;
}

/** Debug: the session explorer on this run's session. */
export function RunDebugPage() {
  const { runKey } = useParams() as { runKey: string };
  return <RunDebugView key={runKey} runKey={runKey} />;
}
