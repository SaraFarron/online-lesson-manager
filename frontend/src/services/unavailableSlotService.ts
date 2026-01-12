/**
 * Service for managing unavailable time slots (external bookings)
 */

import { UnavailableSlot } from '../types';

const STORAGE_KEY = 'unavailable_slots';

export const unavailableSlotService = {
  /**
   * Load all unavailable slots from storage
   */
  loadSlots: (): UnavailableSlot[] => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return stored ? JSON.parse(stored) : [];
    } catch (error) {
      console.error('Failed to load unavailable slots:', error);
      return [];
    }
  },

  /**
   * Save unavailable slots to storage
   */
  saveSlots: (slots: UnavailableSlot[]): void => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(slots));
    } catch (error) {
      console.error('Failed to save unavailable slots:', error);
    }
  },

  /**
   * Add an unavailable slot
   */
  addSlot: (slot: UnavailableSlot): UnavailableSlot[] => {
    const slots = unavailableSlotService.loadSlots();
    const newSlots = [...slots, slot];
    unavailableSlotService.saveSlots(newSlots);
    return newSlots;
  },

  /**
   * Add multiple unavailable slots
   */
  addSlots: (newSlots: UnavailableSlot[]): UnavailableSlot[] => {
    const slots = unavailableSlotService.loadSlots();
    const updatedSlots = [...slots, ...newSlots];
    unavailableSlotService.saveSlots(updatedSlots);
    return updatedSlots;
  },
};
