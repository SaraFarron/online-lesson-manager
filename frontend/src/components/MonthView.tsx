/**
 * MonthView component - displays the main calendar grid
 * Manages drag-and-drop operations and month navigation
 */

import React, { useState, useMemo } from 'react';
import { CalendarEvent, Translations } from '../types';
import { CalendarDay } from './CalendarDay';
import { EventModal } from './EventModal';
import { DragDropContext, DropResult } from '@hello-pangea/dnd';
import {
  format,
  startOfMonth,
  endOfMonth,
  startOfWeek,
  endOfWeek,
  addDays,
  addMonths,
  subMonths,
} from 'date-fns';

interface MonthViewProps {
  events: CalendarEvent[];
  translations: Translations;
  onAddEvent: (eventData: Omit<CalendarEvent, 'id'>) => void;
  onUpdateEvent: (eventId: string, eventData: Omit<CalendarEvent, 'id'>) => void;
  onDeleteEvent: (eventId: string) => void;
  onDeleteRecurringGroup: (recurringGroupId: string) => void;
  onMoveEvent: (eventId: string, newDate: string, newStartTime: string) => void;
}

export const MonthView: React.FC<MonthViewProps> = ({
  events,
  translations,
  onAddEvent,
  onUpdateEvent,
  onDeleteEvent,
  onDeleteRecurringGroup,
  onMoveEvent,
}) => {
  const [currentMonth, setCurrentMonth] = useState(new Date());
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedDate, setSelectedDate] = useState<string>('');
  const [selectedEvent, setSelectedEvent] = useState<CalendarEvent | undefined>();

  // Generate calendar days for the current month view (starting from Monday)
  const calendarDays = useMemo(() => {
    const monthStart = startOfMonth(currentMonth);
    const monthEnd = endOfMonth(currentMonth);
    const startDate = startOfWeek(monthStart, { weekStartsOn: 1 }); // 1 = Monday
    const endDate = endOfWeek(monthEnd, { weekStartsOn: 1 });

    const days: Date[] = [];
    let currentDate = startDate;

    while (currentDate <= endDate) {
      days.push(currentDate);
      currentDate = addDays(currentDate, 1);
    }

    return days;
  }, [currentMonth]);

  // Group events by date for efficient lookup
  const eventsByDate = useMemo(() => {
    const grouped: Record<string, CalendarEvent[]> = {};
    events.forEach((event) => {
      if (!grouped[event.date]) {
        grouped[event.date] = [];
      }
      grouped[event.date].push(event);
    });
    return grouped;
  }, [events]);

  // Group days into weeks and calculate max events per week
  const weeks = useMemo(() => {
    const weeksArray: Date[][] = [];
    for (let i = 0; i < calendarDays.length; i += 7) {
      weeksArray.push(calendarDays.slice(i, i + 7));
    }
    return weeksArray;
  }, [calendarDays]);

  // Calculate dynamic height for each week based on max events in that week
  const getWeekHeight = (week: Date[]): string => {
    const maxEvents = Math.max(
      ...week.map((day) => {
        const dateString = format(day, 'yyyy-MM-dd');
        return (eventsByDate[dateString] || []).length;
      })
    );

    // Base height for 3 events (120px)
    // Reduce for fewer events, increase for more
    if (maxEvents === 0) return '80px';
    if (maxEvents === 1) return '100px';
    if (maxEvents === 2) return '110px';
    if (maxEvents === 3) return '120px';
    
    // Add 30px for each event beyond 3
    const extraHeight = (maxEvents - 3) * 30;
    return `${120 + extraHeight}px`;
  };

  const handleDayClick = (date: string) => {
    // Check if date is in the past
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const clickedDate = new Date(date);
    if (clickedDate < today) return;
    
    setSelectedDate(date);
    setSelectedEvent(undefined);
    setIsModalOpen(true);
  };

  const handleEventClick = (event: CalendarEvent) => {
    // Check if event date is in the past
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const eventDate = new Date(event.date);
    if (eventDate < today) return;
    
    setSelectedEvent(event);
    setSelectedDate(event.date);
    setIsModalOpen(true);
  };

  const handleModalSave = (eventData: Omit<CalendarEvent, 'id'>) => {
    if (selectedEvent) {
      onUpdateEvent(selectedEvent.id, eventData);
    } else {
      onAddEvent(eventData);
    }
  };

  const handleModalDelete = (deleteAll: boolean = false) => {
    if (selectedEvent) {
      if (deleteAll && selectedEvent.recurringGroupId) {
        onDeleteRecurringGroup(selectedEvent.recurringGroupId);
      } else {
        onDeleteEvent(selectedEvent.id);
      }
    }
  };

  const handleDragEnd = (result: DropResult) => {
    const { draggableId, destination } = result;

    if (!destination) return;

    const eventId = draggableId;
    const newDate = destination.droppableId;
    const event = events.find((e) => e.id === eventId);

    if (event && newDate !== event.date) {
      onMoveEvent(eventId, newDate, event.startTime);
    }
  };

  const nextMonth = () => setCurrentMonth(addMonths(currentMonth, 1));
  const prevMonth = () => setCurrentMonth(subMonths(currentMonth, 1));
  const goToToday = () => setCurrentMonth(new Date());

  const weekDays = [
    translations.monday,
    translations.tuesday,
    translations.wednesday,
    translations.thursday,
    translations.friday,
    translations.saturday,
    translations.sunday
  ];

  // Get translated month name
  const getMonthName = (date: Date): string => {
    const monthNames = [
      translations.january,
      translations.february,
      translations.march,
      translations.april,
      translations.may,
      translations.june,
      translations.july,
      translations.august,
      translations.september,
      translations.october,
      translations.november,
      translations.december,
    ];
    return monthNames[date.getMonth()];
  };

  return (
    <div className="h-full flex flex-col">
      {/* Header with navigation */}
      <div className="bg-white border-b border-gray-200 p-3 sm:p-4">
        <div className="flex items-center justify-between">
          <h1 className="text-lg sm:text-2xl font-bold text-gray-800">
            {getMonthName(currentMonth)} {format(currentMonth, 'yyyy')}
          </h1>
          <div className="flex space-x-1 sm:space-x-2">
            <button
              onClick={prevMonth}
              className="px-2 sm:px-4 py-1.5 sm:py-2 text-sm sm:text-base bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300 focus:outline-none focus:ring-2 focus:ring-gray-400"
            >
              <span className="hidden sm:inline">{translations.previous}</span>
              <span className="sm:hidden">←</span>
            </button>
            <button
              onClick={goToToday}
              className="px-2 sm:px-4 py-1.5 sm:py-2 text-sm sm:text-base bg-blue-600 text-white rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {translations.today}
            </button>
            <button
              onClick={nextMonth}
              className="px-2 sm:px-4 py-1.5 sm:py-2 text-sm sm:text-base bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300 focus:outline-none focus:ring-2 focus:ring-gray-400"
            >
              <span className="hidden sm:inline">{translations.next}</span>
              <span className="sm:hidden">→</span>
            </button>
          </div>
        </div>
      </div>

      {/* Calendar Grid */}
      <div className="flex-1 overflow-auto">
        <DragDropContext onDragEnd={handleDragEnd}>
          <div className="h-full">
            {/* Week day headers */}
            <div className="grid grid-cols-7 bg-gray-100 border-b border-gray-200 sticky top-0 z-10">
              {weekDays.map((day) => (
                <div
                  key={day}
                  className="p-1 sm:p-2 text-center text-xs sm:text-sm font-semibold text-gray-700"
                >
                  <span className="hidden sm:inline">{day}</span>
                  <span className="sm:hidden">
                    {day.slice(0, 1)}
                  </span>
                </div>
              ))}
            </div>

            {/* Calendar weeks with dynamic heights */}
            <div className="flex flex-col">
              {weeks.map((week, weekIndex) => (
                <div
                  key={weekIndex}
                  className="grid grid-cols-7"
                  style={{ minHeight: getWeekHeight(week) }}
                >
                  {week.map((day) => {
                    const dateString = format(day, 'yyyy-MM-dd');
                    const dayEvents = eventsByDate[dateString] || [];

                    return (
                      <CalendarDay
                        key={dateString}
                        date={day}
                        currentMonth={currentMonth}
                        events={dayEvents}
                        onDayClick={handleDayClick}
                        onEventClick={handleEventClick}
                      />
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        </DragDropContext>
      </div>

      {/* Event Modal */}
      <EventModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSave={handleModalSave}
        onDelete={selectedEvent ? handleModalDelete : undefined}
        initialDate={selectedDate}
        existingEvent={selectedEvent}
        allEvents={events}
        translations={translations}
      />
    </div>
  );
};
