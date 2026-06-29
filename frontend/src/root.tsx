import { useEffect, useState } from "react";
import {
  isRouteErrorResponse,
  Links,
  Meta,
  Outlet,
  Scripts,
  ScrollRestoration,
} from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import type { Route } from "./+types/root";
import { checkAuth } from "@/lib/auth";
import "./app.css";

export const links: Route.LinksFunction = () => [
  { rel: "preconnect", href: "https://fonts.googleapis.com" },
  {
    rel: "preconnect",
    href: "https://fonts.gstatic.com",
    crossOrigin: "anonymous",
  },
  {
    rel: "stylesheet",
    href: "https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&display=swap",
  },
];

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: false,
    },
  },
});

export function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>CityA</title>
        <Meta />
        <Links />
      </head>
      <body>
        {children}
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}

function SplashScreen() {
  return (
    <div className="flex h-dvh w-full flex-col items-center justify-center gap-4 bg-background">
      <div className="flex size-14 items-center justify-center rounded-2xl bg-primary text-2xl font-bold text-primary-foreground">
        CA
      </div>
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Loading CityA…
      </div>
    </div>
  );
}

export function HydrateFallback() {
  return <SplashScreen />;
}

export default function App() {
  const [booted, setBooted] = useState(false);

  // Bootstrap auth state once on mount.
  useEffect(() => {
    let mounted = true;
    checkAuth().finally(() => {
      if (mounted) setBooted(true);
    });
    return () => {
      mounted = false;
    };
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      {booted ? <Outlet /> : <SplashScreen />}
    </QueryClientProvider>
  );
}

export function ErrorBoundary({ error }: Route.ErrorBoundaryProps) {
  let message = "Oops!";
  let details = "An unexpected error occurred.";
  let stack: string | undefined;

  if (isRouteErrorResponse(error)) {
    message = error.status === 404 ? "404" : "Error";
    details =
      error.status === 404
        ? "The requested page could not be found."
        : error.statusText || details;
  } else if (import.meta.env.DEV && error && error instanceof Error) {
    details = error.message;
    stack = error.stack;
  }

  return (
    <main className="container mx-auto p-4 pt-16">
      <h1 className="text-2xl font-bold">{message}</h1>
      <p className="mt-2 text-muted-foreground">{details}</p>
      {stack && (
        <pre className="mt-4 w-full overflow-x-auto rounded-lg bg-muted p-4 text-xs">
          <code>{stack}</code>
        </pre>
      )}
    </main>
  );
}
