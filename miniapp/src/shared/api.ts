
const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

export type ApiProfile = {
  stage?: string | null
  area?: string | null
  business_type?: string | null
  tax_regime?: string | null
  goal?: string | null
  tz?: string | null
  experience?: string | null
}

export type ApiReminder = {
  id: number
  text: string
  next_at: string
  repeat_rule: string | null
  active: boolean
}

export type ApiMaterial = {
  id: string
  title: string
  description?: string | null
  topic?: string | null
  format?: string | null
  is_paid: boolean
  price?: string | null
  url?: string | null
  reasons?: string[]
}

function initData() {
  return window.Telegram?.WebApp?.initData || ''
}

export async function api<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-Telegram-Init-Data': initData(),
      ...(options.headers || {}),
    },
  })

  const data = await response.json().catch(() => ({}))

  if (!response.ok) {
    throw new Error(data.detail || `Ошибка API: ${response.status}`)
  }

  return data as T
}

export const isTelegramWebApp = () => Boolean(initData())
