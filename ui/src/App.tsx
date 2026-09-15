import { BrowserRouter, Route, Routes } from "react-router-dom";
import { SessionsPage } from "@/pages/sessions-page";
import { SessionPage } from "@/pages/session-page";
import { HistoryPage } from "@/pages/history-page";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<SessionsPage />} />
        <Route path="/sessions/:sessionId" element={<SessionPage />} />
        <Route path="/history" element={<HistoryPage />} />
      </Routes>
    </BrowserRouter>
  );
}
