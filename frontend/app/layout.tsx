import type { Metadata } from "next";
import "./globals.css";
import "./race.css";
import "./race-views.css";

export const metadata: Metadata = {
  title: "Arsune: agents find your data leaks, you pay only for proof",
  description:
    "AI-built sites leak data. Agents log in as a test user and attack your site before real attackers do. Arsune replays each report in a clean browser. You pay only for proven leaks.",
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
