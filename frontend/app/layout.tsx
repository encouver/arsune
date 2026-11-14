import type { Metadata } from "next";
import "./globals.css";
import "./race.css";
import "./race-views.css";

export const metadata: Metadata = {
  title: "Arsune: pay only for proven bugs",
  description:
    "AI agents find the bugs on your site before your visitors do. Arsune checks each report in a clean browser. You pay only for the bugs we prove.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
