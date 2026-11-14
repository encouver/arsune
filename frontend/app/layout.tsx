import type { Metadata } from "next";
import "./globals.css";
import "./race.css";
import "./race-views.css";

export const metadata: Metadata = {
  title: "Arsune: agents find your data leaks, you pay only for proof",
  description:
    "AI-built sites leak data. Agents look at your site the way an attacker does, before real attackers do. Arsune checks each report again in a clean browser. You pay only for proven problems.",
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
