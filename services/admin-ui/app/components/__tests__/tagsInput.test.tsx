import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import TagsInput from "~/features/documents/TagsInput";

function Harness({ initial = [] as string[] }) {
  const [tags, setTags] = useState(initial);
  return (
    <>
      <TagsInput id="tags" value={tags} onChange={setTags} suggestions={["server-discovery", "ssh"]} />
      <output data-testid="tags">{tags.join("|")}</output>
    </>
  );
}

test("adds tags with Enter or comma, normalises them and removes them with ×", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  const input = screen.getByRole("combobox", { name: "Tags" });
  await user.type(input, " Server-Discovery {Enter}");
  await user.type(input, "ssh,");
  await user.type(input, "ssh{Enter}"); // duplicate ignored
  expect(screen.getByTestId("tags").textContent).toBe("server-discovery|ssh");
  await user.click(screen.getByRole("button", { name: "Remove tag ssh" }));
  expect(screen.getByTestId("tags").textContent).toBe("server-discovery");
});

test("explains an invalid tag and keeps it out", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  await user.type(screen.getByRole("combobox", { name: "Tags" }), "has space{Enter}");
  expect(screen.getByText("Tags use lowercase letters, digits and -, up to 40 characters.")).toBeInTheDocument();
  expect(screen.getByTestId("tags").textContent).toBe("");
});

test("stops at 10 tags and suggests only tags not yet chosen", () => {
  const { container } = render(<Harness initial={["ssh", ...Array.from({ length: 9 }, (_, i) => `t${i}`)]} />);
  expect(screen.getByRole("combobox", { name: "Tags" })).toBeDisabled();
  expect(screen.getByText("Use at most 10 tags.")).toBeInTheDocument();
  expect([...container.querySelectorAll("datalist option")].map((option) => option.getAttribute("value"))).toEqual([
    "server-discovery",
  ]);
});
