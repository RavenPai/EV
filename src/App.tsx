import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { CloudAuthGate } from "./components/CloudAuthGate";
import { AppProvider } from "./context/AppContext";
import { Dashboard } from "./pages/Dashboard";
import { Deliveries } from "./pages/Deliveries";
import { Dispatch } from "./pages/Dispatch";
import { Fleet } from "./pages/Fleet";
import { NewDelivery } from "./pages/NewDelivery";
import { Settings } from "./pages/Settings";

export default function App() {
  return (
    <BrowserRouter>
      <CloudAuthGate>
        <AppProvider>
          <AppShell>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/new-delivery" element={<NewDelivery />} />
              <Route path="/deliveries" element={<Deliveries />} />
              <Route path="/dispatch" element={<Dispatch />} />
              <Route path="/fleet" element={<Fleet />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </AppShell>
        </AppProvider>
      </CloudAuthGate>
    </BrowserRouter>
  );
}
