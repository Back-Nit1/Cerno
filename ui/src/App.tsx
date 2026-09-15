import { BrowserRouter, Route, Routes } from "react-router-dom";
import { SessionsPage } from "@/pages/sessions-page";
import { SessionPage } from "@/pages/session-page";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<SessionsPage />} />
        <Route path="/sessions/:sessionId" element={<SessionPage />} />
      </Routes>
    </BrowserRouter>
  );
}
