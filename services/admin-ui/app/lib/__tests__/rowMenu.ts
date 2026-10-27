import { screen, within } from "@testing-library/react";

type Clicker = { click: (element: Element) => Promise<unknown> };
type Queries = Pick<typeof screen, "findByRole">;

/** Opens the row menu of `name` (`Actions for <name>`, found in `scope`) and chooses `item`. */
export async function rowAction(user: Clicker, name: string, item: string, scope: Queries = screen) {
  await user.click(await scope.findByRole("button", { name: `Actions for ${name}` }));
  await user.click(await screen.findByRole("menuitem", { name: item }));
}

/** The names of a row's buttons: at most one inline quick action, then `Actions for <name>`. */
export const rowButtons = (row: HTMLElement) =>
  within(row)
    .queryAllByRole("button")
    .map((button) => button.getAttribute("aria-label") ?? button.textContent?.trim());

/** The labels of the open row menu's items. */
export const menuItems = () => screen.getAllByRole("menuitem").map((item) => item.textContent?.trim());
