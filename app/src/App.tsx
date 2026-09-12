import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./state/auth";
import { SavedProvider, useSaved } from "./state/saved";
import Login from "./routes/Login";
import Listings from "./routes/Listings";
import ListingDetail from "./routes/ListingDetail";
import Rentals from "./routes/Rentals";
import Projects from "./routes/Projects";
import Saved from "./routes/Saved";
import Insights from "./routes/Insights";

function Shell() {
  const { session, logout } = useAuth();
  const saved = useSaved();

  return (
    <>
      <nav className="nav">
        <div className="brand">
          Chennai property
          <span className="brand-note">audited against the API, not the docs</span>
        </div>
        <div className="links">
          <NavLink to="/listings">Buy</NavLink>
          <NavLink to="/rentals">Rent</NavLink>
          <NavLink to="/projects">Projects</NavLink>
          <NavLink to="/saved">
            Saved{saved.ids.length ? ` (${saved.ids.length})` : ""}
          </NavLink>
          <NavLink to="/insights">Insights</NavLink>
        </div>
        <div className="who">
          <span>{session?.email}</span>
          <button className="ghost" onClick={logout}>
            Sign out
          </button>
        </div>
      </nav>
      <main>
        <Routes>
          <Route path="/listings" element={<Listings />} />
          <Route path="/listings/:id" element={<ListingDetail />} />
          <Route path="/rentals" element={<Rentals />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/saved" element={<Saved />} />
          <Route path="/insights" element={<Insights />} />
          <Route path="*" element={<Navigate to="/listings" replace />} />
        </Routes>
      </main>
      <footer className="foot">
        Every price and area on this site is corrected against what the API
        actually returns. The corrections, and the evidence for each, are in{" "}
        <code>submission.json</code> at the repository root.
      </footer>
    </>
  );
}

export default function App() {
  const { session } = useAuth();
  const loc = useLocation();

  if (!session) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="*"
          element={<Navigate to="/login" replace state={{ from: loc.pathname }} />}
        />
      </Routes>
    );
  }

  return (
    <SavedProvider>
      <Shell />
    </SavedProvider>
  );
}
