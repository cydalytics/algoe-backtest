import type { Metadata } from "next";

import "./globals.css";
import { Navbar } from "@/components/layout/navbar";
import { TooltipProvider } from "@/components/ui/tooltip";

/*
 * Fonts are system stacks on purpose. next/font/google downloads the files
 * at build time, and this whole product has to build and run on a machine
 * with no route to the internet - a webfont is not worth a build that fails
 * in the one environment that matters.
 */

export const metadata: Metadata = {
  title: "Eagle-I · Algo E",
  description: "HKJC football trading desk — offline AI trading co-pilot",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="dark h-full antialiased">
      <body className="flex h-screen flex-col overflow-hidden font-sans">
        <TooltipProvider>
          <Navbar />
          <main className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</main>
        </TooltipProvider>
      </body>
    </html>
  );
}
