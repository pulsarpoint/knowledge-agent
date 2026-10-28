/** Expects every control to sit in the one DataTable toolbar row that holds the first: search, filters and actions
 * share `[data-slot='table-toolbar']`. Returns the row. */
export function expectOneToolbarRow(first: HTMLElement, ...controls: HTMLElement[]): HTMLElement {
  const row = first.closest<HTMLElement>("[data-slot='table-toolbar']");
  expect(row).not.toBeNull();
  for (const control of controls) expect(row).toContainElement(control);
  return row!;
}
