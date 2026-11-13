import type { Metadata } from "next";
import "./globals.css";
import "./race.css";
import "./race-views.css";

export const metadata: Metadata = {
  title: "Arsune — Pay only for bugs that Arsune proves",
  description:
    "Agents race to find the bugs your visitors hit. Arsune checks each claim again in a fresh browser and pays only for proven bugs.",
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
