/**
 * HelpModal component - displays instructions on how to use the calendar
 */

import React from 'react';
import Modal from 'react-modal';
import { Translations } from '../types';

// Set app element for accessibility
Modal.setAppElement('#root');

interface HelpModalProps {
  isOpen: boolean;
  onClose: () => void;
  translations: Translations;
}

export const HelpModal: React.FC<HelpModalProps> = ({
  isOpen,
  onClose,
  translations,
}) => {
  return (
    <Modal
      isOpen={isOpen}
      onRequestClose={onClose}
      className="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 bg-white rounded-lg shadow-xl p-4 sm:p-6 w-[calc(100%-2rem)] sm:w-full max-w-lg max-h-[90vh] overflow-y-auto"
      overlayClassName="fixed inset-0 bg-black/30 backdrop-blur-sm z-50"
    >
      <h2 className="text-xl sm:text-2xl font-bold mb-4">
        {translations.helpTitle}
      </h2>

      <div className="space-y-3 text-sm sm:text-base text-gray-700 whitespace-pre-line">
        {translations.helpContent}
      </div>

      <div className="mt-6 flex justify-end">
        <button
          onClick={onClose}
          className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {translations.cancel}
        </button>
      </div>
    </Modal>
  );
};