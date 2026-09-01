import "./globals.css";
import Nav from "../components/Nav";
import StatusBar from "../components/StatusBar";

export const metadata = {
  title: "Driftline",
  description: "Live paper-trading console",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <Nav />
          <main className="main">
            <StatusBar />
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
