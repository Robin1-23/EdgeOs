import type { Metadata } from "next";
import { AccountView } from "./AccountView";

export const metadata: Metadata = { title: "Your account", robots: { index: false } };

export default function AccountPage() {
  return <AccountView />;
}
