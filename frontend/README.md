# RAG Chat

A full-featured Retrieval-Augmented Generation (RAG) chat application built as
a React SPA. It ships with JWT-style auth, a collapsible chat sidebar, streaming
responses with pipeline-step visualization, a knowledge-base manager, voice
input/output, and file attachments.

> **Everything is mocked.** There is no backend. All API calls, SSE streaming,
> and upload progress are simulated in a single file — [`src/lib/mock.ts`](src/lib/mock.ts).
> The app runs fully standalone and all data resets on page refresh.

## Features

- **Auth** — login screen (any credentials work) with an in-memory token and a
  protected app shell.
- **Chat** — streaming assistant responses rendered as Markdown (code blocks,
  tables, lists) with an inline pipeline tracker: _Thinking → Retrieving →
  Tool Call → Generating_.
- **Sessions** — pre-seeded conversations grouped by date (Today / Yesterday /
  Last 7 Days / Older), with rename and delete.
- **Knowledge base** — drag-and-drop uploads with simulated progress and
  indexing, status badges, tag editing, re-index, and delete.
- **Voice** — speech-to-text input and text-to-speech playback via the Web
  Speech API (gracefully hidden where unsupported).
- **Attachments** — attach files to a message with inline preview chips.
- **Polish** — collapsible animated sidebar, dark sidebar surface, brand theme,
  confirmation dialogs for destructive actions, full keyboard navigation.

## Tech stack

| Concern | Choice |
|---|---|
| Framework | React 19 + React Router v8 (framework mode, SPA / `ssr: false`) |
| Language | TypeScript (strict) |
| Styling | Tailwind CSS v4 (CSS-first `@theme`) + shadcn-style UI on Radix |
| Server state | TanStack Query |
| Client state | Zustand (auth, active session, sidebar persistence) |
| Forms | React Hook Form + Zod |
| Animation | Framer Motion |
| Icons | lucide-react |
| Markdown | react-markdown + remark-gfm |

## Requirements

- **Node ≥ 22.22.0** (React Router v8 / Vite 8 requirement). Use the bundled
  `.nvmrc` or any Node ≥ 22.22.

## Setup

```bash
npm install
cp .env.example .env   # optional — VITE_API_BASE_URL is unused while mocked
npm run dev            # http://localhost:5173
```

Other scripts:

```bash
npm run typecheck      # react-router typegen && tsc
npm run build          # production SPA build -> build/client
npm run start          # serve the production build
```

Sign in with the pre-filled demo credentials (or anything) to enter the app.

## Connecting a real backend

Only [`src/lib/api.ts`](src/lib/api.ts) needs to change: replace each delegating
call to `mock.*` with a real `fetch` against `VITE_API_BASE_URL`. The rest of
the app (queries, hooks, components) is backend-agnostic.

## Environment variables

| Variable | Description |
|---|---|
| `VITE_API_BASE_URL` | Base URL for the backend API. Unused while requests are mocked. |

## Project structure

```
src/
├── app.css                # Tailwind v4 theme: tokens, brand, dark sidebar
├── root.tsx               # Root layout, QueryClient, auth bootstrap, splash
├── routes.ts              # flatRoutes() file-based routing
├── entry.client.tsx       # SPA client entry
├── components/
│   ├── ui/                # shadcn-style primitives (Radix-based)
│   ├── layout/            # AppShell, Sidebar, SidebarSection, ProfileFooter
│   ├── chat/              # MessageBubble, StepTracker, InputBar, …
│   └── kb/                # FileCard, UploadDropzone, UploadTaskCard, TagEditor
├── hooks/                 # useStreamChat, useVoiceInput, useVoiceSynthesis, …
├── lib/
│   ├── mock.ts            # ⭐ all mock data + simulated API behavior
│   ├── api.ts             # API layer (delegates to mock.ts)
│   ├── auth.ts            # auth helpers bridging API + store
│   ├── queries.ts         # TanStack Query hooks
│   └── utils.ts           # cn, formatters, grouping, file validation
├── stores/                # authStore, sessionStore, sidebarStore (Zustand)
├── routes/                # login, _auth layout, index, chat, knowledge-base
└── types/                 # api, chat, kb type definitions
```
