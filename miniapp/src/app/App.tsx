import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowRight, ArrowUp, Bell, BookOpen, BriefcaseBusiness, CalendarClock,
  Check, ChevronRight, Clock3, Home, MessageCircle, Plus, Send,
  ShieldCheck, Sparkles, Target, TrendingUp, UserRound, X
} from 'lucide-react'
import '@/styles/globals.css'
import agentMark from '@/assets/agent-symbol.png'
import agentMainCharacter from '@/assets/agent-main-character.png'
import agentNotifications from '@/assets/agent-notifications.png'
import agentCapabilities from '@/assets/agent-capabilities.png'
import agentWordmark from '@/assets/agent-wordmark.png'
import {
  api, ApiProfile, ApiReminder, ApiMaterial, isTelegramWebApp
} from '@/shared/api'

type Tab = 'home' | 'chat' | 'materials' | 'reminders' | 'business'
type Material = ApiMaterial

type Message = {
  id: number
  role: 'assistant' | 'user'
  text: string
  time: string
  materials?: Material[]
  demo?: boolean
}

type Reminder = {
  id: number
  title: string
  date: string
  repeat: string
  active: boolean
}

type BusinessProfile = {
  stage: string
  businessType: string
  sphere: string
  experience: string
  tax: string
  goal: string
  timezone: string
}

type HistoryItem = {
  role?: string
  text?: unknown
  created_at?: string | number
}

const initialMessages: Message[] = [
  {
    id: 1,
    role: 'assistant',
    text: 'Здравствуйте! Я Агент — ваш AI-помощник для предпринимателей. Помогу разобраться в вопросах бизнеса, найти материалы «Альфа-Курса» и не забыть о важных делах.\n\nС чего начнём?',
    time: 'Сейчас'
  }
]

const prompts = [
  'Как выбрать систему налогообложения?',
  'Как привлечь первых клиентов?',
  'Что важно учесть при открытии ИП?',
  'Как рассчитать стартовый бюджет?',
  'Как составить бизнес-план?',
  'Как определить целевую аудиторию?',
  'Как продвигать бизнес без большого бюджета?',
  'Как правильно установить цену на товар?',
  'Как увеличить продажи?',
  'Как найти надёжных поставщиков?',
  'Как оформить сотрудников?',
  'Какие налоги должен платить предприниматель?',
  'Как вести учёт доходов и расходов?',
  'Как открыть интернет-магазин?',
  'Как выбрать название для бизнеса?',
  'Как анализировать конкурентов?',
  'Как создать программу лояльности?',
  'Как привлечь клиентов через социальные сети?',
  'Какие ошибки совершают начинающие предприниматели?',
  'Подбери материалы для развития бизнеса'
]

const PROMPT_STORAGE_KEY = 'agent_prompt_queue_v1'

function shufflePrompts(items: string[]): string[] {
  const result = [...items]

  for (let i = result.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[result[i], result[j]] = [result[j], result[i]]
  }

  return result
}

const initialProfile: BusinessProfile = {
  stage: '',
  businessType: '',
  sphere: '',
  experience: '',
  tax: '',
  goal: '',
  timezone: 'Europe/Moscow'
}

const businessTypeNames: Record<string, string> = {
  retail: 'Розничная торговля',
  online_store: 'Интернет-магазин',
  services: 'Услуги',
  cafe_restaurant: 'Кафе или ресторан',
  production: 'Производство',
  construction: 'Строительство и ремонт',
  it: 'IT и цифровые продукты',
  freelance: 'Самозанятость и фриланс',
  other: 'Другое'
}

const stageNames: Record<string, string> = {
  idea: 'Планирую открыть бизнес',
  launch: 'Запускаю бизнес',
  running: 'Уже веду бизнес',
  growing: 'Развиваю бизнес'
}

const experienceNames: Record<string, string> = {
  novice: 'Начинающий',
  some: 'Есть некоторый опыт',
  experienced: 'Опытный предприниматель'
}

function clock() {
  return new Intl.DateTimeFormat('ru-RU', {
    hour: '2-digit',
    minute: '2-digit'
  }).format(new Date())
}

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : []
}

function normalizeMaterial(value: unknown): Material[] {
  return asArray<Record<string, unknown>>(value)
    .filter(item => item && typeof item === 'object')
    .map(item => ({
      id: String(item.id ?? ''),
      title: String(item.title ?? 'Материал без названия'),
      description: typeof item.description === 'string' ? item.description : null,
      topic: typeof item.topic === 'string' ? item.topic : null,
      format: typeof item.format === 'string' ? item.format : null,
      is_paid: Boolean(item.is_paid),
      price: typeof item.price === 'string' ? item.price : null,
      url: typeof item.url === 'string' ? item.url : null,
      reasons: asArray<string>(item.reasons).filter(x => typeof x === 'string')
    }))
}

function normalizeReminders(value: unknown): Reminder[] {
  return asArray<ApiReminder>(value).map(x => ({
    id: Number(x.id),
    title: String(x.text ?? ''),
    date: String(x.next_at ?? ''),
    repeat:
      x.repeat_rule === 'days:1' ? 'Каждый день' :
      x.repeat_rule === 'days:7' ? 'Каждую неделю' :
      x.repeat_rule === 'monthly' ? 'Каждый месяц' :
      x.repeat_rule === 'yearly' ? 'Каждый год' :
      'Не повторять',
    active: Boolean(x.active)
  }))
}

export default function App() {
  const [tab, setTab] = useState<Tab>('home')
  const [messages, setMessages] = useState<Message[]>(initialMessages)
  const [input, setInput] = useState('')
  const [profile, setProfile] = useState<BusinessProfile>(initialProfile)
  const [reminders, setReminders] = useState<Reminder[]>([])
  const [showNewReminder, setShowNewReminder] = useState(false)
  const [reminderTitle, setReminderTitle] = useState('')
  const [reminderDate, setReminderDate] = useState('')
  const [reminderRepeat, setReminderRepeat] = useState('Не повторять')
  const [notice, setNotice] = useState('')
  const endRef = useRef<HTMLDivElement>(null)

  const [connected, setConnected] = useState(false)
  const [connectionError, setConnectionError] = useState('')
  const [sending, setSending] = useState(false)
  const [materials, setMaterials] = useState<Material[]>([])
  const [materialQuery, setMaterialQuery] = useState('')

  // Очередь подсказок сохраняется в браузере и переживает перезагрузку Mini App.
  const [promptQueue, setPromptQueue] = useState<string[]>(() => {
    try {
      const saved = localStorage.getItem(PROMPT_STORAGE_KEY)

      if (saved) {
        const parsed: unknown = JSON.parse(saved)

        if (
          Array.isArray(parsed) &&
          parsed.length > 0 &&
          parsed.every((item): item is string =>
            typeof item === 'string' && prompts.includes(item)
          )
        ) {
          return parsed
        }
      }
    } catch {
      // При повреждённом сохранении создаём новую очередь.
    }

    return shufflePrompts(prompts)
  })

  const profileFilledCount = [
    profile.businessType,
    profile.stage,
    profile.sphere,
    profile.experience,
    profile.tax,
    profile.goal
  ].filter(Boolean).length

  const profileReady = profileFilledCount > 0

  const profilePercent = Math.round(profileFilledCount / 6 * 100)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, tab])

  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(() => setNotice(''), 2600)
    return () => window.clearTimeout(timer)
  }, [notice])

  useEffect(() => {
    try {
      localStorage.setItem(PROMPT_STORAGE_KEY, JSON.stringify(promptQueue))
    } catch {
      // Работа подсказок не должна ломаться, если хранилище браузера недоступно.
    }
  }, [promptQueue])

  useEffect(() => {
    const webApp = window.Telegram?.WebApp
    webApp?.ready()
    webApp?.expand()

    if (!isTelegramWebApp()) {
      setConnectionError('Откройте Mini App через кнопку в Telegram-боте, чтобы авторизоваться.')
      return
    }

    let alive = true

    async function loadInitialData() {
      try {
        const [p, r, h] = await Promise.all([
          api<ApiProfile>('/api/profile'),
          api<{ items?: ApiReminder[] }>('/api/reminders'),
          api<{ items?: HistoryItem[] }>('/api/history')
        ])

        if (!alive) return

        setProfile({
          stage: p?.stage || '',
          businessType: p?.business_type || '',
          sphere: p?.area || '',
          experience: p?.experience || '',
          tax: ({
            usn: 'УСН',
            npd: 'НПД',
            osno: 'ОСНО',
            patent: 'Патент',
            other: 'Другое'
          } as Record<string, string>)[p?.tax_regime || ''] || '',
          goal: p?.goal || '',
          timezone: p?.tz || 'Europe/Moscow'
        })

        setReminders(normalizeReminders(r?.items))

        const history = asArray<HistoryItem>(h?.items)

        if (history.length > 0) {
          setMessages(history.map((x, i) => ({
            id: i + 1,
            role: x.role === 'user' ? 'user' : 'assistant',
            text: String(x.text ?? ''),
            time: x.created_at
              ? new Date(x.created_at).toLocaleTimeString('ru-RU', {
                  hour: '2-digit',
                  minute: '2-digit'
                })
              : ''
          })))
        }

        if (alive) {
          setConnected(true)
          setConnectionError('')
        }
      } catch (error) {
        if (alive) {
          setConnectionError(
            error instanceof Error ? error.message : 'Не удалось подключиться к серверу'
          )
        }
      }
    }

    void loadInitialData()

    return () => {
      alive = false
    }
  }, [])

  async function sendMessage(value = input) {
    const text = value.trim()
    if (!text || sending) return

    if (!connected) {
      setNotice(connectionError || 'Нет подключения к серверу')
      return
    }

    const userMessage: Message = {
      id: Date.now(),
      role: 'user',
      text,
      time: clock()
    }

    setMessages(prev => [...asArray<Message>(prev), userMessage])
    setInput('')
    setSending(true)

    try {
      const result = await api<{
        answer?: string
        no_answer?: boolean
        materials?: unknown
      }>('/api/chat', {
        method: 'POST',
        body: JSON.stringify({ message: text })
      })

      const answer = String(result?.answer ?? 'Сервер не вернул текст ответа.')
      const foundMaterials = normalizeMaterial(result?.materials)

      setMessages(prev => [
        ...asArray<Message>(prev),
        {
          id: Date.now() + 1,
          role: 'assistant',
          text: answer,
          time: clock(),
          materials: foundMaterials
        }
      ])
    } catch (error) {
      setMessages(prev => [
        ...asArray<Message>(prev),
        {
          id: Date.now() + 1,
          role: 'assistant',
          text: `Не удалось получить ответ: ${error instanceof Error ? error.message : 'ошибка сервера'}`,
          time: clock()
        }
      ])
    } finally {
      setSending(false)
    }
  }

  async function openMaterial(item: Material) {
    if (!item.url) {
      setNotice('Для этого материала ссылка пока не подтверждена')
      return
    }

    try {
      await api('/api/material-click', {
        method: 'POST',
        body: JSON.stringify({ material_id: item.id })
      })
    } catch {
      // Аналитика необязательна для перехода по ссылке.
    }

    if (window.Telegram?.WebApp) {
      window.Telegram.WebApp.openLink(item.url)
    } else {
      window.open(item.url, '_blank', 'noopener,noreferrer')
    }
  }

  async function refreshReminders() {
    const result = await api<{ items?: ApiReminder[] }>('/api/reminders')
    setReminders(normalizeReminders(result?.items))
  }

  async function addReminder() {
    if (!reminderTitle.trim() || !reminderDate) {
      setNotice('Укажите событие и дату')
      return
    }

    if (!connected) {
      setNotice(connectionError || 'Нет подключения к серверу')
      return
    }

    try {
      await api('/api/reminders', {
        method: 'POST',
        body: JSON.stringify({
          text: reminderTitle.trim(),
          date: reminderDate,
          repeat: reminderRepeat
        })
      })

      await refreshReminders()
      setReminderTitle('')
      setReminderDate('')
      setReminderRepeat('Не повторять')
      setShowNewReminder(false)
      setNotice('Напоминание сохранено — бот пришлёт его в Telegram')
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Не удалось создать напоминание')
    }
  }

  async function toggleReminder(id: number) {
    const item = reminders.find(r => r.id === id)
    if (!item) return

    try {
      await api(`/api/reminders/${id}?active=${!item.active}`, {
        method: 'PATCH'
      })
      setReminders(prev => prev.map(r =>
        r.id === id ? { ...r, active: !r.active } : r
      ))
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Не удалось изменить напоминание')
    }
  }

  async function removeReminder(id: number) {
    try {
      await api(`/api/reminders/${id}`, { method: 'DELETE' })
      setReminders(prev => prev.filter(r => r.id !== id))
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Не удалось удалить напоминание')
    }
  }

  async function saveProfile() {
    const regime = ({
      'УСН': 'usn',
      'НПД': 'npd',
      'ОСНО': 'osno',
      'Патент': 'patent',
      'Другое': 'other'
    } as Record<string, string>)[profile.tax] || ''

    try {
      await api('/api/profile', {
        method: 'PUT',
        body: JSON.stringify({
          stage: profile.stage,
          business_type: profile.businessType,
          area: profile.sphere,
          experience: profile.experience,
          tax_regime: regime,
          goal: profile.goal,
          tz: profile.timezone
        })
      })
      setNotice('Профиль сохранён на сервере')
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Не удалось сохранить профиль')
    }
  }

  async function loadMaterials(query = materialQuery) {
    if (!connected) {
      setNotice(connectionError || 'Нет подключения к серверу')
      return
    }

    try {
      const result = await api<{ items?: unknown }>(
        `/api/materials${query.trim() ? `?query=${encodeURIComponent(query.trim())}` : ''}`
      )
      setMaterials(normalizeMaterial(result?.items))
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Не удалось загрузить материалы')
    }
  }

  function sendPrompt(question: string) {
    // Не расходуем подсказку, если запрос нельзя отправить.
    if (sending || !connected) {
      void sendMessage(question)
      return
    }

    setPromptQueue(current => {
      const remaining = current.filter(item => item !== question)

      // Когда все 20 вопросов использованы, перемешиваем полный список заново.
      return remaining.length > 0 ? remaining : shufflePrompts(prompts)
    })

    void sendMessage(question)
  }

  function openChatWithQuestion(question: string) {
    setTab('chat')
    void sendMessage(question)
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-mark"><img src={agentMark} alt="" /></div>
        <img className="brand-wordmark" src={agentWordmark} alt="АГЕНТ" />
        <div className="brand-copy">
          <b>АГЕНТ</b>
          <span>AI-ПОМОЩНИК ДЛЯ БИЗНЕСА</span>
        </div>
        <div className="top-status">
          <span className="status-dot" />
          {connected ? 'Подключено' : 'Нет связи'}
        </div>
      </header>

      <main className="main-content">

        {tab === 'home' && (
          <section className="home-page">
            <div className="home-greeting">
              <div>
                <div className="eyebrow">ВАШ БИЗНЕС · ВАШ РОСТ</div>
                <h1>Ваш бизнес. Новый уровень.</h1>
                <p>Персональный AI-помощник для решений, роста и уверенных действий.</p>
              </div>
              <div className="home-avatar"><img src={agentMark} alt="Агент" /></div>
            </div>

            <div className="home-hero">
              <img className="hero-agent-art" src={agentMainCharacter} alt="Иллюстрация Агента" />
              <div className="home-hero-label">
                <Sparkles size={15} /> ПЕРСОНАЛЬНЫЙ ИИ-ПОМОЩНИК
              </div>
              <h2>Идеи превращаются в действия.</h2>
              <p>
                Получайте ответы на вопросы, находите полезные материалы
                «Альфа-Курса» и планируйте важные задачи в одном месте.
              </p>
              <button className="home-hero-button" onClick={() => setTab('chat')}>
                Задать вопрос <ArrowRight size={16} />
              </button>
            </div>

            <div className="home-section-heading">
              <div>
                <div className="eyebrow">ВАШ ПРОФИЛЬ</div>
                <h2>Движение к цели</h2>
              </div>
              <button onClick={() => setTab('business')}>
                Изменить <ChevronRight size={15} />
              </button>
            </div>

            <div className="home-profile-card">
              <div className="home-profile-top">
                <div className="home-profile-icon"><Target size={22} /></div>
                <div>
                  <b>{profile.goal || 'Определите главную цель бизнеса'}</b>
                  <span>
                    {profileReady
                      ? 'Агент сможет точнее учитывать ваш контекст'
                      : 'Заполните профиль, чтобы получать персональные рекомендации'}
                  </span>
                </div>
              </div>

              <div className="home-profile-progress">
                <div className="home-progress-label">
                  <span>Заполнение профиля</span>
                  <b>{profilePercent}%</b>
                </div>
                <div className="home-progress-track">
                  <div style={{ width: `${profilePercent}%` }} />
                </div>
              </div>

              <div className="home-profile-details">
                <div>
                  <span>Тип бизнеса</span>
                  <b>{businessTypeNames[profile.businessType] || 'Не указан'}</b>
                </div>
                <div>
                  <span>Стадия</span>
                  <b>{stageNames[profile.stage] || 'Не указана'}</b>
                </div>
                <div>
                  <span>Опыт</span>
                  <b>{experienceNames[profile.experience] || 'Не указан'}</b>
                </div>
                <div>
                  <span>Напоминания</span>
                  <b>{reminders.filter(r => r.active).length} активных</b>
                </div>
              </div>

              <button className="home-profile-button" onClick={() => setTab('business')}>
                {profileReady ? 'Дополнить профиль' : 'Настроить профиль'}
                <ArrowRight size={15} />
              </button>
            </div>

            <div className="home-section-heading">
              <div>
                <div className="eyebrow">ИНСТРУМЕНТЫ</div>
                <h2>Что будем делать?</h2>
              </div>
            </div>

            <div className="home-actions">
              <button onClick={() => setTab('chat')}>
                <div className="home-action-icon red"><MessageCircle size={21} /></div>
                <span>
                  <b>Спросить Агента</b>
                  <small>Разобраться в вопросе бизнеса</small>
                </span>
                <ChevronRight />
              </button>

              <button onClick={() => setTab('materials')}>
                <div className="home-action-icon"><BookOpen size={21} /></div>
                <span>
                  <b>Найти материалы</b>
                  <small>Обучение из базы «Альфа-Курса»</small>
                </span>
                <ChevronRight />
              </button>

              <button onClick={() => setShowNewReminder(true)}>
                <div className="home-action-icon"><CalendarClock size={21} /></div>
                <span>
                  <b>Создать напоминание</b>
                  <small>Не забыть о важных делах</small>
                </span>
                <ChevronRight />
              </button>
            </div>

            <div className="home-tip">
              <div className="home-tip-icon"><TrendingUp size={20} /></div>
              <div>
                <b>Маленькие шаги ведут к результату</b>
                <p>
                  Начните с заполнения профиля и сформулируйте главную задачу,
                  которую хотите решить в бизнесе.
                </p>
              </div>
            </div>
          </section>
        )}

        {tab === 'chat' && (
          <section className="chat-page">
            <div className="welcome-line">
              <div>
                <div className="eyebrow">АЛЬФА-КУРС · AI-АССИСТЕНТ</div>
                <h1>Чем помочь вашему бизнесу?</h1>
                <p>Задайте вопрос — разберёмся вместе.</p>
              </div>
              <div className="chat-mascot"><img src={agentCapabilities} alt="Агент помогает с вопросами бизнеса" /></div>
            </div>

            <div className="trust-note">
              <ShieldCheck size={16} />
              <span>
                {connected
                  ? 'Ответы формируются на сервере по базе знаний «Альфа-Курса»'
                  : connectionError || 'Подключаемся к серверу…'}
              </span>
            </div>

            <div className="chat-window">
              <div className="chat-heading">
                <div className="assistant-avatar"><img src={agentMark} alt="" /></div>
                <div><b>Агент</b><span>Ваш помощник по развитию бизнеса</span></div>
                <span className="online-pill">{connected ? 'подключено' : 'ожидание'}</span>
              </div>

              <div className="messages">
                {asArray<Message>(messages).map(m => (
                  <div key={m.id} className={`message-row ${m.role}`}>
                    {m.role === 'assistant' && (
                      <div className="message-avatar"><img src={agentMark} alt="" /></div>
                    )}
                    <div className="bubble-wrap">
                      <div className="bubble">
                        {String(m.text ?? '').split('\n').map((line, i) => (
                          <span key={i}>{line || <br />}</span>
                        ))}

                        {asArray<Material>(m.materials).length > 0 && (
                          <div className="chat-materials">
                            {asArray<Material>(m.materials).map(item => (
                              <a
                                key={item.id}
                                href={item.url || '#'}
                                onClick={e => {
                                  e.preventDefault()
                                  void openMaterial(item)
                                }}
                              >
                                <b>{item.is_paid ? '💳' : '🆓'} {item.title}</b>
                                <small>
                                  {item.is_paid
                                    ? `Польза: ${item.topic || 'материал по выбранной теме'}${item.price ? ` · ${item.price}` : ''}`
                                    : item.description || 'Бесплатный материал'}
                                </small>
                              </a>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="message-meta">
                        {m.time}
                        {m.demo && <em> · демо-ответ</em>}
                      </div>
                    </div>
                  </div>
                ))}
                <div ref={endRef} />
              </div>

              <div className="suggestions-label">Попробуйте спросить</div>
              <div className="prompt-list">
                {promptQueue.slice(0, 4).map(p => (
                  <button
                    key={p}
                    className="prompt-chip"
                    onClick={() => sendPrompt(p)}
                    disabled={sending || !connected}
                  >
                    {p}<ArrowUp size={13} />
                  </button>
                ))}
              </div>

              <form
                className="composer"
                onSubmit={e => {
                  e.preventDefault()
                  void sendMessage()
                }}
              >
                <textarea
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      void sendMessage()
                    }
                  }}
                  placeholder="Напишите свой вопрос..."
                  rows={1}
                />
                <button
                  className="send-button"
                  type="submit"
                  disabled={!input.trim() || sending}
                  aria-label="Отправить"
                >
                  <Send size={18} />
                </button>
              </form>
              <div className="composer-foot">
                {sending ? 'Агент готовит ответ…' : 'Enter — отправить · Shift + Enter — новая строка'}
              </div>
            </div>

            <div className="quick-grid">
              <button onClick={() => setTab('materials')}>
                <BookOpen />
                <span><b>Найти материалы</b><small>Подборка под вашу задачу</small></span>
                <ChevronRight />
              </button>
              <button onClick={() => setShowNewReminder(true)}>
                <CalendarClock />
                <span><b>Создать напоминание</b><small>Сроки и важные дела</small></span>
                <ChevronRight />
              </button>
            </div>
          </section>
        )}

        {tab === 'reminders' && (
          <section className="section-page">
            <div className="eyebrow">ПЛАНИРОВАНИЕ</div>
            <h1>Напоминания</h1>
            <p className="page-lead">Не пропускайте важные события и возвращайтесь к обучению вовремя.</p>

            <div className="reminder-info">
              <img className="reminder-mascot" src={agentNotifications} alt="Агент напоминает о важных делах" />
              <Bell size={18} />
              <div>
                <b>Разовые и повторяющиеся события</b>
                <span>Например, уплата налога, отчётность или продолжение курса.</span>
              </div>
            </div>

            <button className="primary-action" onClick={() => setShowNewReminder(true)}>
              <Plus size={18} /> Новое напоминание
            </button>

            <div className="section-row">
              <h2>Ваш список</h2>
              <span>{reminders.filter(r => r.active).length} активных</span>
            </div>

            {reminders.length === 0 ? (
              <div className="empty-state">
                <div className="empty-icon"><img src={agentNotifications} alt="" /></div>
                <b>Пока нет напоминаний</b>
                <p>Добавьте первое событие — оно появится здесь.</p>
              </div>
            ) : (
              <div className="reminder-list">
                {reminders.map(r => (
                  <article className={`reminder-card ${!r.active ? 'inactive' : ''}`} key={r.id}>
                    <div className="reminder-symbol"><Clock3 size={19} /></div>
                    <div className="reminder-content">
                      <b>{r.title}</b>
                      <span>
                        {r.date && !Number.isNaN(Date.parse(r.date))
                          ? new Date(r.date).toLocaleString('ru-RU', {
                              day: 'numeric', month: 'long', year: 'numeric',
                              hour: '2-digit', minute: '2-digit'
                            })
                          : 'Дата не указана'} · {r.repeat}
                      </span>
                      <small>{r.active ? 'Активно' : 'Отключено'}</small>
                    </div>
                    <div className="reminder-controls">
                      <button onClick={() => void toggleReminder(r.id)}>
                        {r.active ? 'Выключить' : 'Включить'}
                      </button>
                      <button className="icon-button" onClick={() => void removeReminder(r.id)} aria-label="Удалить">
                        <X size={16} />
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            )}

            <p className="demo-disclaimer">
              Напоминания хранятся на сервере. В назначенное время Telegram-бот пришлёт вам сообщение.
            </p>
          </section>
        )}

        {tab === 'materials' && (
          <section className="section-page">
            <div className="eyebrow">БАЗА ЗНАНИЙ</div>
            <h1>Материалы для бизнеса</h1>
            <p className="page-lead">
              Подберём обучение под конкретную задачу, опыт и стадию вашего дела.
            </p>

            <div className="material-search">
              <BookOpen size={19} />
              <input
                value={materialQuery}
                onChange={e => setMaterialQuery(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') void loadMaterials()
                }}
                placeholder="Например, налоги, маркетинг, продажи"
              />
              <button onClick={() => void loadMaterials()}>Найти</button>
            </div>

            <div className="material-feature">
              <img className="capabilities-art" src={agentCapabilities} alt="Агент помогает находить полезные материалы" />
              <div className="feature-icon"><Sparkles size={22} /></div>
              <div>
                <span className="feature-label">ПЕРСОНАЛЬНЫЙ ПОДБОР</span>
                <h2>Не просто список курсов, а следующий полезный шаг</h2>
                <p>
                  В рабочем режиме помощник учитывает ваш запрос и профиль бизнеса,
                  показывает бесплатные и платные материалы из каталога и объясняет пользу каждого.
                </p>
                <button
                  className="text-action"
                  onClick={() => {
                    setTab('chat')
                    setInput('Подбери материалы под мою задачу')
                  }}
                >
                  Подобрать материалы <ChevronRight size={16} />
                </button>
              </div>
            </div>

            {materials.length === 0 ? (
              <div className="empty-catalog">
                <BookOpen size={22} />
                <b>Введите тему или нажмите «Найти», чтобы получить стартовую подборку</b>
                <p>Покажем реальные материалы, доступные в базе.</p>
              </div>
            ) : (
              <div className="reminder-list">
                {materials.map(m => (
                  <article className="reminder-card" key={m.id}>
                    <div className="reminder-symbol"><BookOpen size={19} /></div>
                    <div className="reminder-content">
                      <b>{m.title}</b>
                      <span>
                        {m.topic || m.format || 'Материал Альфа-Курса'} ·
                        {m.is_paid ? ` Платный${m.price ? ` · ${m.price}` : ''}` : ' Бесплатный'}
                      </span>
                      {m.description && <small>{m.description}</small>}
                      {asArray<string>(m.reasons).length > 0 && (
                        <small>{asArray<string>(m.reasons).join(' · ')}</small>
                      )}
                    </div>
                    {m.url && (
                      <button className="icon-button" onClick={() => void openMaterial(m)} aria-label="Открыть материал">
                        <ChevronRight size={16} />
                      </button>
                    )}
                  </article>
                ))}
              </div>
            )}
          </section>
        )}

        {tab === 'business' && (
          <section className="section-page">
            <div className="eyebrow">ПЕРСОНАЛИЗАЦИЯ</div>
            <h1>Мой бизнес</h1>
            <p className="page-lead">
              Несколько деталей помогут давать более точные ответы. Заполнять всё сразу необязательно.
            </p>

            <div className="profile-progress">
              <div className="profile-icon"><BriefcaseBusiness /></div>
              <div>
                <b>{profileReady ? 'Профиль заполняется' : 'Расскажите о своём деле'}</b>
                <span>{profileFilledCount} из 6 параметров заполнено</span>
              </div>
              <span className="profile-check">
                {profileReady ? <Check size={18} /> : <UserRound size={18} />}
              </span>
            </div>

            <div className="form-card">
              <label>
                Тип бизнеса
                <select value={profile.businessType} onChange={e => setProfile(p => ({ ...p, businessType: e.target.value }))}>
                  <option value="">Выберите тип бизнеса</option>
                  <option value="retail">Розничная торговля</option>
                  <option value="online_store">Интернет-магазин</option>
                  <option value="services">Услуги</option>
                  <option value="cafe_restaurant">Кафе или ресторан</option>
                  <option value="production">Производство</option>
                  <option value="construction">Строительство и ремонт</option>
                  <option value="it">IT и цифровые продукты</option>
                  <option value="freelance">Самозанятость и фриланс</option>
                  <option value="other">Другое</option>
                </select>
              </label>

              <label>
                На какой вы стадии?
                <select value={profile.stage} onChange={e => setProfile(p => ({ ...p, stage: e.target.value }))}>
                  <option value="">Выберите вариант</option>
                  <option value="idea">Планирую открыть бизнес</option>
                  <option value="launch">Запускаю бизнес</option>
                  <option value="running">Уже веду бизнес</option>
                  <option value="growing">Развиваю бизнес</option>
                </select>
              </label>

              <label>
                Сфера деятельности
                <input
                  value={profile.sphere}
                  onChange={e => setProfile(p => ({ ...p, sphere: e.target.value }))}
                  placeholder="Например, розничная торговля"
                />
              </label>

              <label>
                Опыт в предпринимательстве
                <select value={profile.experience} onChange={e => setProfile(p => ({ ...p, experience: e.target.value }))}>
                  <option value="">Выберите свой опыт</option>
                  <option value="novice">Начинающий, опыта почти нет</option>
                  <option value="some">Есть некоторый опыт</option>
                  <option value="experienced">Опытный предприниматель</option>
                </select>
              </label>

              <label>
                Режим налогообложения
                <select value={profile.tax} onChange={e => setProfile(p => ({ ...p, tax: e.target.value }))}>
                  <option value="">Пока не знаю / не выбрано</option>
                  <option value="УСН">УСН</option>
                  <option value="НПД">НПД — самозанятый</option>
                  <option value="ОСНО">ОСНО</option>
                  <option value="Патент">Патент</option>
                  <option value="Другое">Другое</option>
                </select>
              </label>

              <label>
                Главная задача сейчас
                <input
                  value={profile.goal}
                  onChange={e => setProfile(p => ({ ...p, goal: e.target.value }))}
                  placeholder="Например, найти первых клиентов"
                />
              </label>

              <label>
                Часовой пояс
                <select value={profile.timezone} onChange={e => setProfile(p => ({ ...p, timezone: e.target.value }))}>
                  <option value="Europe/Moscow">Москва (UTC+3)</option>
                  <option value="Europe/Kaliningrad">Калининград (UTC+2)</option>
                  <option value="Asia/Yekaterinburg">Екатеринбург (UTC+5)</option>
                  <option value="Asia/Novosibirsk">Новосибирск (UTC+7)</option>
                  <option value="Asia/Vladivostok">Владивосток (UTC+10)</option>
                </select>
              </label>

              <button className="primary-action" onClick={() => void saveProfile()}>
                <Check size={18} /> Сохранить профиль
              </button>
            </div>

            <button className="chat-link" onClick={() => setTab('chat')}>
              <MessageCircle size={17} /> Вернуться к вопросам <ChevronRight size={16} />
            </button>
          </section>
        )}
      </main>

      <nav className="bottom-nav">
        <button className={tab === 'home' ? 'active' : ''} onClick={() => setTab('home')}>
          <Home /><span>Главная</span>
        </button>
        <button className={tab === 'chat' ? 'active' : ''} onClick={() => setTab('chat')}>
          <MessageCircle /><span>Чат</span>
        </button>
        <button className={tab === 'materials' ? 'active' : ''} onClick={() => setTab('materials')}>
          <BookOpen /><span>Материалы</span>
        </button>
        <button className={tab === 'reminders' ? 'active' : ''} onClick={() => setTab('reminders')}>
          <Bell /><span>Задачи</span>
        </button>
        <button className={tab === 'business' ? 'active' : ''} onClick={() => setTab('business')}>
          <BriefcaseBusiness /><span>Профиль</span>
        </button>
      </nav>

      {showNewReminder && (
        <div className="modal-backdrop" onClick={() => setShowNewReminder(false)}>
          <div className="modal-card" onClick={e => e.stopPropagation()}>
            <div className="modal-head">
              <div>
                <span className="eyebrow">НОВОЕ СОБЫТИЕ</span>
                <h2>Создать напоминание</h2>
              </div>
              <button className="icon-button" onClick={() => setShowNewReminder(false)}>
                <X />
              </button>
            </div>

            <label>
              О чём напомнить?
              <input
                value={reminderTitle}
                onChange={e => setReminderTitle(e.target.value)}
                placeholder="Например, оплатить налог"
                autoFocus
              />
            </label>

            <label>
              Дата и время
              <input
                type="datetime-local"
                value={reminderDate}
                onChange={e => setReminderDate(e.target.value)}
                min={new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16)}
              />
            </label>

            <label>
              Повторение
              <select value={reminderRepeat} onChange={e => setReminderRepeat(e.target.value)}>
                <option>Не повторять</option>
                <option>Каждый день</option>
                <option>Каждую неделю</option>
                <option>Каждый месяц</option>
                <option>Каждый год</option>
              </select>
            </label>

            <button className="primary-action" onClick={() => void addReminder()}>
              <Check size={18} /> Сохранить
            </button>
            <p className="modal-hint">
              Уведомление придёт от Telegram-бота в указанное время по вашему часовому поясу.
            </p>
          </div>
        </div>
      )}

      {notice && <div className="toast"><Check size={16} />{notice}</div>}
    </div>
  )
}
