import { Links, Meta, Outlet, Scripts, ScrollRestoration } from "react-router";
import "./globals.css";

export function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>Knowledge Agent Administration</title>
        <Meta />
        <Links />
        <script
          dangerouslySetInnerHTML={{
            __html: 'document.documentElement.dataset.theme=localStorage.getItem("knowledge-admin.theme")||"light";',
          }}
        />
      </head>
      <body className="bg-background text-foreground min-h-screen">
        {children}
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}

export default function Root() {
  return <Outlet />;
}

export function ErrorBoundary() {
  return (
    <main className="p-8 max-w-xl mx-auto">
      <h1 className="text-xl font-bold mb-2">Something went wrong</h1>
      <p className="text-sm opacity-70">
        Reload the page, or go back to the{" "}
        <a href="/" className="underline">
          app list
        </a>
        .
      </p>
    </main>
  );
}
