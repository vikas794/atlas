import type { RouteObject } from "react-router-dom";

import App from "./App";
import { NotFound } from "./components/shared/not-found";

/**
 * Route table. Declared as data (not <Routes>) so the app runs on a data
 * router, which `useBlocker` needs to guard unsaved edits in the Review screen.
 */
export const routes: RouteObject[] = [
  {
    element: <App />,
    children: [
      {
        path: "/",
        lazy: async () => ({
          Component: (await import("./features/playlists/playlists-home"))
            .PlaylistsHome,
        }),
      },
      {
        path: "/playlists/:id",
        lazy: async () => ({
          Component: (await import("./features/playlists/playlist-detail"))
            .PlaylistDetail,
        }),
      },
      {
        path: "/playlists/:id/review/:videoId",
        lazy: async () => ({
          Component: (await import("./features/playlists/review-screen"))
            .ReviewScreen,
        }),
      },
      {
        path: "/settings",
        lazy: async () => ({
          Component: (await import("./features/settings/settings-screen"))
            .SettingsScreen,
        }),
      },
      { path: "*", element: <NotFound /> },
    ],
  },
];
