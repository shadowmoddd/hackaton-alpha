/// <reference types="vite/client" />

interface Window {
  Telegram?: {
    WebApp?: {
      initData: string
      ready: () => void
      expand: () => void
      openLink: (url: string, options?: { try_instant_view?: boolean }) => void
      initDataUnsafe?: {
        user?: { id: number; first_name: string; username?: string; photo_url?: string }
      }
    }
  }
}
