import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import routes from "~/routes";
import DocumentationFolders from "~/routes/documentationFolders";

test("the old documentation folder pages redirect to the Documents list", async () => {
  for (const path of ["/documentation/folders", "/documentation/folders/team/payments"]) {
    const { unmount } = render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/documentation/folders" element={<DocumentationFolders />} />
          <Route path="/documentation/folders/*" element={<DocumentationFolders />} />
          <Route path="/documents" element={<p>Documents list</p>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("Documents list")).toBeInTheDocument();
    unmount();
  }
});

test("both folder paths are routed to the redirect", () => {
  const files = ["documentation/folders", "documentation/folders/*"].map(
    (path) => routes.find((item) => item.path === path)?.file,
  );
  expect(files).toEqual(["./routes/documentationFolders.tsx", "./routes/documentationFolders.tsx"]);
});
