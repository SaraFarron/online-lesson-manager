/**
 * WeekView component - displays weekly calendar with time-based grid
 * Shows events positioned by start time and duration on a horizontal time axis
 * Supports mobile views: 1-day and 3-day modes
 */

import React, { useState, useMemo, useEffect, useRef } from 'react';
import { CalendarEvent, UnavailableSlot, WorkDayConfig, Translations } from '../types';
import { EventModal } from './EventModal';
import {
  format,
  startOfWeek,
  addDays,
  addWeeks,
  subWeeks,
} from 'date-fns';

interface WeekViewProps {
  currentDate: Date;
  events: CalendarEvent[];
  unavailableSlots: UnavailableSlot[];
  workDayConfig?: WorkDayConfig;
  translations: Translations;
  onAddEvent: (eventData: Omit<CalendarEvent, 'id'>) => void;
  onUpdateEvent: (eventId: string, eventData: Omit<CalendarEvent, 'id'>) => void;
  onDeleteEvent: (eventId: string) => void;
  onDeleteRecurringGroup: (recurringGroupId: string) => void;
  onWeekChange: (newDate: Date) => void;
}

export const WeekView: React.FC<WeekViewProps> = ({
  currentDate,
  events,
  unavailableSlots,
  workDayConfig = { startHour: 9, endHour: 20 },
  translations,
  onAddEvent,
  onUpdateEvent,
  onDeleteEvent,
  onDeleteRecurringGroup,
  onWeekChange,
}) => {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedDate, setSelectedDate] = useState<string>('');
  const [selectedTime, setSelectedTime] = useState<string>('09:00');
  const [selectedEvent, setSelectedEvent] = useState<CalendarEvent | undefined>();
  const [draggedButton, setDraggedButton] = useState<'lesson' | 'weekly' | null>(null);
  const [draggedEvent, setDraggedEvent] = useState<CalendarEvent | null>(null);
  const [isWeeklyLesson, setIsWeeklyLesson] = useState(false);
  const [dragPreview, setDragPreview] = useState<{ show: boolean; time: string; yPosition: number }>({
    show: false,
    time: '',
    yPosition: 0,
  });
  const gridContainerRef = useRef<HTMLDivElement>(null);

  // Get the week days (Monday to Sunday)
  const weekDays = useMemo(() => {
    const start = startOfWeek(currentDate, { weekStartsOn: 1 });
    return Array.from({ length: 7 }, (_, i) => addDays(start, i));
  }, [currentDate]);

  // Display all 7 days
  const displayedDays = weekDays;

  // Hours array (0-23)
  const hours = Array.from({ length: 24 }, (_, i) => i);

  // Group events by date
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

  // Group unavailable slots by date
  const unavailableByDate = useMemo(() => {
    const grouped: Record<string, UnavailableSlot[]> = {};
    unavailableSlots.forEach((slot) => {
      if (!grouped[slot.date]) {
        grouped[slot.date] = [];
      }
      grouped[slot.date].push(slot);
    });
    return grouped;
  }, [unavailableSlots]);

  // Auto-scroll to work day start on mount or when current date changes
  useEffect(() => {
    if (gridContainerRef.current) {
      const startHourPosition = workDayConfig.startHour * 51; // 51px per hour
      gridContainerRef.current.scrollTop = startHourPosition;
    }
  }, [currentDate, workDayConfig.startHour]);

  // Calculate position and height for an event
  const getEventStyle = (event: CalendarEvent) => {
    const [hours, minutes] = event.startTime.split(':').map(Number);
    const startMinutes = hours * 60 + minutes;
    const top = (startMinutes / 60) * 51; // 51px per hour
    const height = (event.duration / 60) * 51;
    
    return {
      top: `${top}px`,
      height: `${height}px`,
    };
  };

  // Check if a time slot is unavailable
  const isSlotUnavailable = (date: string, hour: number): boolean => {
    const slots = unavailableByDate[date] || [];
    return slots.some(slot => {
      const [startHour] = slot.startTime.split(':').map(Number);
      const [endHour] = slot.endTime.split(':').map(Number);
      return hour >= startHour && hour < endHour;
    });
  };

  // Check if hour is outside working hours
  const isOutsideWorkingHours = (hour: number): boolean => {
    return hour < workDayConfig.startHour || hour >= workDayConfig.endHour;
  };

  // Find first available slot in the displayed days
  const findFirstAvailableSlot = (): { date: string; time: string } => {
    for (const day of displayedDays) {
      const dateString = format(day, 'yyyy-MM-dd');
      for (let hour = 8; hour < 20; hour++) {
        if (!isSlotUnavailable(dateString, hour)) {
          const dayEvents = eventsByDate[dateString] || [];
          const timeString = `${hour.toString().padStart(2, '0')}:00`;
          
          const hasConflict = dayEvents.some(event => {
            const [eventHour] = event.startTime.split(':').map(Number);
            const eventEndHour = eventHour + Math.ceil(event.duration / 60);
            return hour >= eventHour && hour < eventEndHour;
          });
          
          if (!hasConflict) {
            return { date: dateString, time: timeString };
          }
        }
      }
    }
    
    return { date: format(displayedDays[0], 'yyyy-MM-dd'), time: '09:00' };
  };

  const handleAddLesson = (isWeekly: boolean = false) => {
    const { date, time } = findFirstAvailableSlot();
    setSelectedDate(date);
    setSelectedTime(time);
    setSelectedEvent(undefined);
    setIsWeeklyLesson(isWeekly);
    setIsModalOpen(true);
  };

  const handleSlotClick = (date: string, hour: number, minute: number = 0) => {
    if (isSlotUnavailable(date, hour) || isOutsideWorkingHours(hour)) return;
    
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const slotDate = new Date(date);
    if (slotDate < today) return;
    
    setSelectedDate(date);
    setSelectedTime(`${hour.toString().padStart(2, '0')}:${minute.toString().padStart(2, '0')}`);
    setSelectedEvent(undefined);
    setIsWeeklyLesson(false);
    setIsModalOpen(true);
  };

  const handleEventClick = (event: CalendarEvent) => {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const eventDate = new Date(event.date);
    if (eventDate < today) return;
    
    setSelectedEvent(event);
    setSelectedDate(event.date);
    setSelectedTime(event.startTime);
    setIsWeeklyLesson(false);
    setIsModalOpen(true);
  };

  const handleModalSave = (eventData: Omit<CalendarEvent, 'id'>) => {
    if (selectedEvent) {
      onUpdateEvent(selectedEvent.id, eventData);
    } else {
      onAddEvent(eventData);
    }
    setIsWeeklyLesson(false);
  };

  const handleModalDelete = (deleteAll: boolean = false) => {
    if (selectedEvent) {
      if (deleteAll && selectedEvent.recurringGroupId) {
        onDeleteRecurringGroup(selectedEvent.recurringGroupId);
      } else {
        onDeleteEvent(selectedEvent.id);
      }
    }
    setIsWeeklyLesson(false);
  };

  const handleModalClose = () => {
    setIsModalOpen(false);
    setIsWeeklyLesson(false);
  };

  // Drag and drop handlers
  const handleDragStart = (buttonType: 'lesson' | 'weekly') => {
    setDraggedButton(buttonType);
  };

  const handleEventDragStart = (event: CalendarEvent) => {
    setDraggedEvent(event);
  };

  const handleEventDragEnd = () => {
    setDraggedEvent(null);
    setDragPreview({ show: false, time: '', yPosition: 0 });
  };

  const handleDragOver = (e: React.DragEvent, hour: number) => {
    e.preventDefault();
    
    if (!draggedButton && !draggedEvent) return;

    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const offsetY = e.clientY - rect.top;
    const minuteOffset = Math.floor((offsetY / 51) * 60);
    const minute = Math.floor(minuteOffset / 5) * 5;
    
    const timeString = `${hour.toString().padStart(2, '0')}:${minute.toString().padStart(2, '0')}`;
    
    const gridContainer = document.querySelector('.week-grid-container');
    if (gridContainer) {
      const gridRect = gridContainer.getBoundingClientRect();
      const yPosition = e.clientY - gridRect.top;
      
      setDragPreview({
        show: true,
        time: timeString,
        yPosition: yPosition,
      });
    }
  };

  const handleDragLeave = () => {
    setTimeout(() => {
      if (draggedButton || draggedEvent) {
        setDragPreview({ show: false, time: '', yPosition: 0 });
      }
    }, 50);
  };

  const handleDrop = (e: React.DragEvent, date: string, hour: number, offsetY: number) => {
    e.preventDefault();
    
    setDragPreview({ show: false, time: '', yPosition: 0 });
    
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const slotDate = new Date(date);
    if (slotDate < today) {
      setDraggedButton(null);
      setDraggedEvent(null);
      return;
    }

    if (isSlotUnavailable(date, hour) || isOutsideWorkingHours(hour)) {
      setDraggedButton(null);
      setDraggedEvent(null);
      return;
    }

    const minuteOffset = Math.floor((offsetY / 51) * 60);
    const minute = Math.floor(minuteOffset / 5) * 5;
    const newTime = `${hour.toString().padStart(2, '0')}:${minute.toString().padStart(2, '0')}`;

    if (draggedEvent) {
      const updatedEvent: Omit<CalendarEvent, 'id'> = {
        ...draggedEvent,
        date: date,
        startTime: newTime,
      };
      onUpdateEvent(draggedEvent.id, updatedEvent);
      setDraggedEvent(null);
      return;
    }

    if (draggedButton) {
      setSelectedDate(date);
      setSelectedTime(newTime);
      setSelectedEvent(undefined);
      setIsWeeklyLesson(draggedButton === 'weekly');
      setIsModalOpen(true);
      setDraggedButton(null);
    }
  };

  const handleDragEnd = () => {
    setDraggedButton(null);
    setDraggedEvent(null);
    setDragPreview({ show: false, time: '', yPosition: 0 });
  };

  const nextWeek = () => onWeekChange(addWeeks(currentDate, 1));
  const prevWeek = () => onWeekChange(subWeeks(currentDate, 1));
  const goToToday = () => onWeekChange(new Date());

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

  // Get short month name (first 3 letters)
  const getShortMonthName = (date: Date): string => {
    return getMonthName(date).slice(0, 3);
  };

  const dayNames = [
    translations.mon,
    translations.tue,
    translations.wed,
    translations.thu,
    translations.fri,
    translations.sat,
    translations.sun
  ];

  return (
    <div className="h-full flex flex-col pb-0 md:pb-0">
      {/* Header with navigation - Desktop */}
      <div className="hidden md:block bg-white border-b border-gray-200 p-4">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold text-gray-800">
            {translations.weekOf} {format(weekDays[0], 'd')} {getMonthName(weekDays[0])}, {format(weekDays[0], 'yyyy')}
          </h1>
          <div className="flex space-x-2">
            <button
              onClick={prevWeek}
              className="px-4 py-2 bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300 focus:outline-none focus:ring-2 focus:ring-gray-400"
            >
              {translations.previous}
            </button>
            <button
              onClick={goToToday}
              className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {translations.today}
            </button>
            <button
              onClick={nextWeek}
              className="px-4 py-2 bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300 focus:outline-none focus:ring-2 focus:ring-gray-400"
            >
              {translations.next}
            </button>
            <button
              draggable
              onDragStart={() => handleDragStart('lesson')}
              onDragEnd={handleDragEnd}
              onClick={() => handleAddLesson(false)}
              className="px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 focus:outline-none focus:ring-2 focus:ring-green-500 cursor-move"
            >
              {translations.addLesson}
            </button>
            <button
              draggable
              onDragStart={() => handleDragStart('weekly')}
              onDragEnd={handleDragEnd}
              onClick={() => handleAddLesson(true)}
              className="px-4 py-2 bg-purple-600 text-white rounded-md hover:bg-purple-700 focus:outline-none focus:ring-2 focus:ring-purple-500 cursor-move"
            >
              {translations.addWeeklyLesson}
            </button>
          </div>
        </div>
      </div>

      {/* Header with navigation - Mobile */}
      <div className="md:hidden bg-white border-b border-gray-200 p-3">
        <div className="flex items-center justify-between mb-2">
          <h1 className="text-lg font-bold text-gray-800">
            {translations.weekOf} {format(weekDays[0], 'd')} {getMonthName(weekDays[0])}
          </h1>
          <div className="flex space-x-2">
            <button
              onClick={prevWeek}
              className="px-3 py-1.5 text-sm bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300"
            >
              ←
            </button>
            <button
              onClick={goToToday}
              className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
            >
              {translations.today}
            </button>
            <button
              onClick={nextWeek}
              className="px-3 py-1.5 text-sm bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300"
            >
              →
            </button>
          </div>
        </div>
      </div>

      {/* Week Grid */}
      <div className="flex-1 overflow-auto mb-16 md:mb-0 px-2 md:px-0" ref={gridContainerRef}>
        <div className="relative h-full">
          {/* Day headers */}
          <div className="grid sticky top-0 z-20 border-b border-gray-300" style={{ gridTemplateColumns: '60px repeat(7, 1fr)' }} 
               data-grid-mobile="true">
            <style>{`
              @media (min-width: 768px) {
                [data-grid-mobile="true"] {
                  grid-template-columns: 80px repeat(7, 1fr) !important;
                }
              }
            `}</style>
            {/* Time column header */}
            <div className="bg-gray-50 p-2 text-center text-xs md:text-sm font-semibold border-r border-gray-300">
              {translations.time}
            </div>
            {/* Day columns */}
            {displayedDays.map((day) => (
              <div 
                key={day.toString()} 
                className="p-1 md:p-2 text-center border-r border-gray-300 bg-gray-50"
              >
                <div className="text-[10px] md:text-sm font-semibold truncate">{dayNames[(weekDays.indexOf(day))]}</div>
                <div className="text-[9px] md:text-xs text-gray-600 truncate">
                  {format(day, 'd')} {getShortMonthName(day)}
                </div>
              </div>
            ))}
          </div>

          {/* Time grid */}
          <div className="relative week-grid-container">
            {hours.map((hour) => (
              <div key={hour} className="grid" style={{ gridTemplateColumns: '60px repeat(7, 1fr)', height: '51px' }}
                   data-grid-mobile="true">
                {/* Time label */}
                <div 
                  className={`p-2 text-xs border-r border-b border-gray-300 ${
                    isOutsideWorkingHours(hour) ? 'bg-red-50 text-gray-500' : 'bg-gray-50 text-gray-600'
                  }`}
                >
                  {`${hour.toString().padStart(2, '0')}:00`}
                </div>

                {/* Day columns */}
                {displayedDays.map((day) => {
                  const dateString = format(day, 'yyyy-MM-dd');
                  const isUnavailable = isSlotUnavailable(dateString, hour);
                  const isNonWorkingHour = isOutsideWorkingHours(hour);
                  
                  const today = new Date();
                  today.setHours(0, 0, 0, 0);
                  const isPastDate = day < today;
                  
                  return (
                    <div
                      key={dateString}
                      className={`relative border-r border-b border-gray-300 transition-colors ${
                        isPastDate || isNonWorkingHour ? 'bg-red-100 cursor-not-allowed' :
                        isUnavailable ? 'bg-red-100' : 
                        'bg-green-50 hover:bg-green-100 cursor-pointer'
                      }`}
                      onClick={() => !isPastDate && !isNonWorkingHour && handleSlotClick(dateString, hour)}
                      onDragOver={!isPastDate && !isNonWorkingHour ? (e) => handleDragOver(e, hour) : undefined}
                      onDragLeave={handleDragLeave}
                      onDrop={!isPastDate && !isNonWorkingHour ? (e) => handleDrop(e, dateString, hour, e.nativeEvent.offsetY) : undefined}
                    />
                  );
                })}
              </div>
            ))}

            {/* Events overlay */}
            {displayedDays.map((day, dayIndex) => {
              const dateString = format(day, 'yyyy-MM-dd');
              const dayEvents = eventsByDate[dateString] || [];
              
              const today = new Date();
              today.setHours(0, 0, 0, 0);
              const isPastDate = day < today;

              return dayEvents.map((event) => {
                const style = getEventStyle(event);
                const isMobile = window.innerWidth < 768;
                const timeColumnWidth = isMobile ? 60 : 80;
                
                return (
                  <div
                    key={event.id}
                    className={`absolute text-white rounded shadow-md overflow-hidden z-10 ${
                      isPastDate 
                        ? 'bg-gray-400 cursor-not-allowed' 
                        : 'bg-blue-500 cursor-pointer hover:bg-blue-600'
                    }`}
                    style={{
                      ...style,
                      left: `calc((100% - ${timeColumnWidth}px) / 7 * ${dayIndex} + ${timeColumnWidth}px)`,
                      width: `calc((100% - ${timeColumnWidth}px) / 7 - 2px)`,
                      padding: isMobile ? '2px 4px' : '8px',
                      fontSize: isMobile ? '9px' : '12px',
                    }}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (!isPastDate) handleEventClick(event);
                    }}
                    draggable={!isPastDate}
                    onDragStart={() => handleEventDragStart(event)}
                    onDragEnd={handleEventDragEnd}
                  >
                    {!isMobile && (
                      <div className="font-semibold truncate">{event.title}</div>
                    )}
                    <div className={`${isMobile ? 'text-center truncate' : 'text-xs opacity-90'}`}>
                      {event.startTime}
                    </div>
                  </div>
                );
              });
            })}

            {/* Drag preview indicator */}
            {dragPreview.show && (
              <div
                className="absolute left-0 right-0 z-30 pointer-events-none"
                style={{ top: `${dragPreview.yPosition}px` }}
              >
                <div className="relative">
                  <div className="h-0.5 bg-blue-500 shadow-lg"></div>
                  <div className="absolute -top-6 left-1/2 transform -translate-x-1/2 bg-blue-600 text-white text-xs px-3 py-1 rounded-full shadow-lg font-semibold whitespace-nowrap">
                    {dragPreview.time}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Mobile Bottom Navigation Bar */}
      <div className="md:hidden fixed bottom-0 left-0 right-0 bg-white border-t border-gray-200 shadow-lg z-20">
        <div className="grid grid-cols-2 gap-2 p-3">
          <button
            onClick={() => handleAddLesson(false)}
            className="px-4 py-3 bg-green-600 text-white text-sm rounded-md hover:bg-green-700 active:bg-green-800 font-medium shadow-sm"
          >
            {translations.addLesson}
          </button>
          <button
            onClick={() => handleAddLesson(true)}
            className="px-4 py-3 bg-purple-600 text-white text-sm rounded-md hover:bg-purple-700 active:bg-purple-800 font-medium shadow-sm"
          >
            {translations.addWeeklyLesson}
          </button>
        </div>
      </div>

      {/* Event Modal */}
      <EventModal
        isOpen={isModalOpen}
        onClose={handleModalClose}
        onSave={handleModalSave}
        onDelete={selectedEvent ? handleModalDelete : undefined}
        initialDate={selectedDate}
        initialTime={selectedTime}
        existingEvent={selectedEvent}
        allEvents={events}
        unavailableSlots={unavailableSlots}
        isWeeklyLesson={isWeeklyLesson}
        translations={translations}
      />
    </div>
  );
};
