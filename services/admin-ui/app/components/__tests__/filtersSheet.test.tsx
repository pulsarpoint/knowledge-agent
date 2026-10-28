import { useState } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FiltersSheet from "../FiltersSheet";

function Page() {
  const [owner, setOwner] = useState("");
  const [archived, setArchived] = useState(false);
  return (
    <>
      <FiltersSheet
        active={[owner, archived].filter(Boolean).length}
        onReset={() => {
          setOwner("");
          setArchived(false);
        }}
      >
        <input aria-label="Owner" value={owner} onChange={(event) => setOwner(event.target.value)} />
        <label>
          <input type="checkbox" checked={archived} onChange={(event) => setArchived(event.target.checked)} /> Archived
        </label>
      </FiltersSheet>
      <span>
        owner={owner} archived={String(archived)}
      </span>
    </>
  );
}

test("Filters: no badge while nothing hidden is set; the button opens a right Sheet with the filters", async () => {
  const user = userEvent.setup();
  render(<Page />);
  const button = screen.getByRole("button", { name: "Filters" });
  expect(within(button).queryByText(/\d/)).not.toBeInTheDocument();
  await user.click(button);
  const sheet = screen.getByRole("dialog", { name: "Filters" });
  expect(within(sheet).getByRole("textbox", { name: "Owner" })).toBeInTheDocument();
  expect(within(sheet).getByRole("button", { name: "Reset" })).toBeDisabled();
});

test("Filters: the badge counts the active hidden filters, and Reset clears them", async () => {
  const user = userEvent.setup();
  render(<Page />);
  await user.click(screen.getByRole("button", { name: "Filters" }));
  const sheet = within(screen.getByRole("dialog", { name: "Filters" }));
  await user.type(sheet.getByRole("textbox", { name: "Owner" }), "ana");
  await user.click(sheet.getByRole("checkbox", { name: "Archived" }));
  expect(screen.getByText("owner=ana archived=true")).toBeInTheDocument();
  await user.keyboard("{Escape}");
  const button = screen.getByRole("button", { name: "Filters, 2 active" });
  expect(within(button).getByText("2")).toBeInTheDocument();
  await user.click(button);
  await user.click(within(screen.getByRole("dialog", { name: "Filters" })).getByRole("button", { name: "Reset" }));
  expect(screen.getByText("owner= archived=false")).toBeInTheDocument();
  await user.keyboard("{Escape}");
  expect(screen.getByRole("button", { name: "Filters" })).toBeInTheDocument();
});
