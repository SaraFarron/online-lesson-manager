/**
 * Internationalization service - manages translations
 */

import { Language, Translations } from '../types';

const LANGUAGE_STORAGE_KEY = 'app_language';

const translations: Record<Language, Translations> = {
  en: {
    // App header
    appTitle: 'Class Schedule',
    help: 'Help',
    helpTitle: 'How to Use',
    helpContent: '• Click on a green time slot to add a lesson\n• Click on an existing lesson to edit or delete it\n• Use "Add Lesson" button to quickly add a single lesson\n• Use "Add Weekly Lesson" to create recurring lessons for 12 weeks\n• Drag and drop lessons to reschedule them\n• Red slots indicate unavailable time (external bookings or non-working hours)\n• Past dates are disabled and shown in gray',
    
    // Navigation
    month: 'Month',
    week: 'Week',
    logout: 'Logout',
    today: 'Today',
    previous: 'Previous',
    next: 'Next',
    
    // Login
    loginTitle: 'Calendar App',
    loginSubtitle: 'Enter your access token to continue',
    accessToken: 'Access Token',
    enterToken: 'Enter your token',
    tokenLength: 'Token must be 4-16 characters long',
    signIn: 'Sign In',
    verifying: 'Verifying...',
    tokenError: 'Token must be between 4 and 16 characters',
    testingNote: 'For testing purposes, use any token with 4-16 characters',
    
    // Event Modal
    createEvent: 'Create Event',
    editEvent: 'Edit Event',
    eventTitle: 'Event Title *',
    enterEventTitle: 'Enter event title',
    date: 'Date *',
    startTime: 'Start Time *',
    duration: 'Duration *',
    repeatWeekly: 'Repeat weekly (next 12 weeks)',
    repeatWeeklyNote: ' (cannot change)',
    deleteThis: 'Delete This',
    deleteAllWeekly: 'Delete All Weekly',
    delete: 'Delete',
    cancel: 'Cancel',
    create: 'Create',
    update: 'Update',
    
    // Week View
    weekOf: 'Week of',
    addLesson: 'Add Lesson',
    addWeeklyLesson: 'Add Weekly Lesson',
    generateExternalBookings: 'Generate External Bookings',
    lesson: 'Lesson',
    
    // Validation errors
    errorTitleRequired: 'Event title is required (max 100 characters)',
    errorInvalidDate: 'Invalid date format',
    errorInvalidTime: 'Invalid time format (use HH:MM)',
    errorInvalidDuration: 'Duration must be at least 5 minutes and a multiple of 5',
    errorEventOverlap: 'Event overlaps with an existing event',
    errorSlotUnavailable: 'Time slot is unavailable (external booking)',
    errorPastDate: 'Cannot create events in the past',
    
    // Time formats
    hours: 'h',
    minutes: 'm',
    
    // Days of week
    monday: 'Monday',
    tuesday: 'Tuesday',
    wednesday: 'Wednesday',
    thursday: 'Thursday',
    friday: 'Friday',
    saturday: 'Saturday',
    sunday: 'Sunday',
    
    // Short days
    mon: 'Mon',
    tue: 'Tue',
    wed: 'Wed',
    thu: 'Thu',
    fri: 'Fri',
    sat: 'Sat',
    sun: 'Sun',

    // Time
    time: 'Time',
    
    // Month names
    january: 'January',
    february: 'February',
    march: 'March',
    april: 'April',
    may: 'May',
    june: 'June',
    july: 'July',
    august: 'August',
    september: 'September',
    october: 'October',
    november: 'November',
    december: 'December',
  },
  
  ru: {
    // App header
    appTitle: 'Расписание занятий',
    help: 'Справка',
    helpTitle: 'Как использовать',
    helpContent: '• Нажмите на зелёный временной слот, чтобы добавить занятие\n• Нажмите на существующее занятие, чтобы редактировать или удалить его\n• Используйте кнопку "Добавить урок" для быстрого добавления одного занятия\n• Используйте "Добавить еженедельный урок" для создания повторяющихся занятий на 12 недель\n• Перетаскивайте занятия, чтобы изменить их время\n• Красные слоты указывают на недоступное время (внешние бронирования или нерабочие часы)\n• Прошедшие даты отключены и показаны серым цветом',
    
    // Navigation
    month: 'Месяц',
    week: 'Неделя',
    logout: 'Выйти',
    today: 'Сегодня',
    previous: 'Назад',
    next: 'Вперёд',
    
    // Login
    loginTitle: 'Календарь',
    loginSubtitle: 'Введите токен доступа для продолжения',
    accessToken: 'Токен доступа',
    enterToken: 'Введите ваш токен',
    tokenLength: 'Токен должен быть длиной 4-16 символов',
    signIn: 'Войти',
    verifying: 'Проверка...',
    tokenError: 'Токен должен быть длиной от 4 до 16 символов',
    testingNote: 'Для тестирования используйте любой токен длиной 4-16 символов',
    
    // Event Modal
    createEvent: 'Создать событие',
    editEvent: 'Редактировать событие',
    eventTitle: 'Название события *',
    enterEventTitle: 'Введите название события',
    date: 'Дата *',
    startTime: 'Время начала *',
    duration: 'Продолжительность *',
    repeatWeekly: 'Повторять еженедельно (следующие 12 недель)',
    repeatWeeklyNote: ' (нельзя изменить)',
    deleteThis: 'Удалить это',
    deleteAllWeekly: 'Удалить все еженедельные',
    delete: 'Удалить',
    cancel: 'Отмена',
    create: 'Создать',
    update: 'Обновить',
    
    // Week View
    weekOf: 'Неделя',
    addLesson: 'Добавить урок',
    addWeeklyLesson: 'Добавить еженедельный урок',
    generateExternalBookings: 'Генерировать внешние бронирования',
    lesson: 'Урок',
    
    // Validation errors
    errorTitleRequired: 'Требуется название события (макс. 100 символов)',
    errorInvalidDate: 'Неверный формат даты',
    errorInvalidTime: 'Неверный формат времени (используйте ЧЧ:ММ)',
    errorInvalidDuration: 'Продолжительность должна быть не менее 5 минут и кратна 5',
    errorEventOverlap: 'Событие пересекается с существующим событием',
    errorSlotUnavailable: 'Временной слот недоступен (внешнее бронирование)',
    errorPastDate: 'Невозможно создать события в прошлом',
    
    // Time formats
    hours: 'ч',
    minutes: 'м',
    
    // Days of week
    monday: 'Понедельник',
    tuesday: 'Вторник',
    wednesday: 'Среда',
    thursday: 'Четверг',
    friday: 'Пятница',
    saturday: 'Суббота',
    sunday: 'Воскресенье',
    
    // Short days
    mon: 'Пн',
    tue: 'Вт',
    wed: 'Ср',
    thu: 'Чт',
    fri: 'Пт',
    sat: 'Сб',
    sun: 'Вс',

    // Time
    time: 'Время',
    
    // Month names
    january: 'Январь',
    february: 'Февраль',
    march: 'Март',
    april: 'Апрель',
    may: 'Май',
    june: 'Июнь',
    july: 'Июль',
    august: 'Август',
    september: 'Сентябрь',
    october: 'Октябрь',
    november: 'Ноябрь',
    december: 'Декабрь',
  },
  
  zh: {
    // App header
    appTitle: '课程表',
    help: '帮助',
    helpTitle: '使用说明',
    helpContent: '• 点击绿色时间段添加课程\n• 点击现有课程进行编辑或删除\n• 使用"添加课程"按钮快速添加单个课程\n• 使用"添加每周课程"创建12周的重复课程\n• 拖放课程以重新安排时间\n• 红色时间段表示不可用时间（外部预订或非工作时间）\n• 过去的日期已禁用并显示为灰色',
    
    // Navigation
    month: '月',
    week: '周',
    logout: '退出',
    today: '今天',
    previous: '上一个',
    next: '下一个',
    
    // Login
    loginTitle: '日历应用',
    loginSubtitle: '输入您的访问令牌以继续',
    accessToken: '访问令牌',
    enterToken: '输入您的令牌',
    tokenLength: '令牌长度必须为4-16个字符',
    signIn: '登录',
    verifying: '验证中...',
    tokenError: '令牌长度必须在4到16个字符之间',
    testingNote: '用于测试，请使用4-16个字符的任意令牌',
    
    // Event Modal
    createEvent: '创建事件',
    editEvent: '编辑事件',
    eventTitle: '事件标题 *',
    enterEventTitle: '输入事件标题',
    date: '日期 *',
    startTime: '开始时间 *',
    duration: '持续时间 *',
    repeatWeekly: '每周重复（未来12周）',
    repeatWeeklyNote: '（无法更改）',
    deleteThis: '删除此项',
    deleteAllWeekly: '删除所有每周项',
    delete: '删除',
    cancel: '取消',
    create: '创建',
    update: '更新',
    
    // Week View
    weekOf: '周',
    addLesson: '添加课程',
    addWeeklyLesson: '添加每周课程',
    generateExternalBookings: '生成外部预订',
    lesson: '课程',
    
    // Validation errors
    errorTitleRequired: '事件标题为必填项（最多100个字符）',
    errorInvalidDate: '无效的日期格式',
    errorInvalidTime: '无效的时间格式（使用 HH:MM）',
    errorInvalidDuration: '持续时间必须至少为5分钟且为5的倍数',
    errorEventOverlap: '事件与现有事件重叠',
    errorSlotUnavailable: '时间段不可用（外部预订）',
    errorPastDate: '无法在过去创建事件',
    
    // Time formats
    hours: '时',
    minutes: '分',
    
    // Days of week
    monday: '星期一',
    tuesday: '星期二',
    wednesday: '星期三',
    thursday: '星期四',
    friday: '星期五',
    saturday: '星期六',
    sunday: '星期日',
    
    // Short days
    mon: '周一',
    tue: '周二',
    wed: '周三',
    thu: '周四',
    fri: '周五',
    sat: '周六',
    sun: '周日',

    // Time
    time: '时间',
    
    // Month names
    january: '一月',
    february: '二月',
    march: '三月',
    april: '四月',
    may: '五月',
    june: '六月',
    july: '七月',
    august: '八月',
    september: '九月',
    october: '十月',
    november: '十一月',
    december: '十二月',
  },
};

export const i18nService = {
  /**
   * Get current language from localStorage or default to English
   */
  getCurrentLanguage: (): Language => {
    try {
      const stored = localStorage.getItem(LANGUAGE_STORAGE_KEY);
      if (stored && (stored === 'en' || stored === 'ru' || stored === 'zh')) {
        return stored as Language;
      }
    } catch (error) {
      console.error('Failed to get language:', error);
    }
    return 'en';
  },

  /**
   * Save language preference to localStorage
   */
  setLanguage: (language: Language): void => {
    try {
      localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
    } catch (error) {
      console.error('Failed to save language:', error);
    }
  },

  /**
   * Get translations for a specific language
   */
  getTranslations: (language: Language): Translations => {
    return translations[language];
  },

  /**
   * Get all available languages
   */
  getAvailableLanguages: (): Language[] => {
    return ['en', 'ru', 'zh'];
  },
};
