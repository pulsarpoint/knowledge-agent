import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ThemeToggle from "../ThemeToggle";

test("toggles data-theme and persists", async () => {
  document.documentElement.dataset.theme = "light";
  render(<ThemeToggle />);
  await userEvent.click(screen.getByRole("button"));
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(localStorage.getItem("docs-wiki.theme")).toBe("dark");
  await userEvent.click(screen.getByRole("button"));
  expect(document.documentElement.dataset.theme).toBe("light");
});
