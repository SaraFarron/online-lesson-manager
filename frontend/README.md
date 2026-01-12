# Calendar Web Application

A production-quality, single-page calendar application built with React, TypeScript, Vite, and TailwindCSS.

## 🚀 Features

- **Month View Calendar**: Clean grid layout displaying days of the month
- **Event Creation**: Click on any day to create new events with:
  - Event title
  - Date and time (5-minute increments)
  - Duration (5-minute increments, 5 min to 8 hours)
  - Weekly recurrence (automatically generates 12 weeks)
- **Event Management**: Click events to edit or delete them
- **Drag & Drop**: Move events between days with intuitive drag-and-drop
- **Visual Availability**: Days with available slots show light green background
- **Overlap Prevention**: Validates events to prevent scheduling conflicts
- **localStorage Persistence**: Events persist between browser sessions
- **Accessibility**: ARIA-compliant modals and keyboard navigation

## 🛠️ Tech Stack

- **React 18+** with TypeScript
- **Vite** for fast development and building
- **TailwindCSS** for styling
- **date-fns** for date manipulation
- **react-modal** for accessible modals
- **@hello-pangea/dnd** for drag-and-drop functionality
- **uuid** for unique ID generation

## 📦 Installation & Setup

### Prerequisites
- Node.js 18+ and npm

### Setup Commands

```bash
# Install dependencies
npm install

# Start development server
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview
```

## 🎯 Usage

### Creating Events
1. Click on any day cell in the calendar
2. Fill in the event details:
   - **Title**: Event name (required, max 100 characters)
   - **Date**: Auto-filled from clicked day (editable)
   - **Start Time**: Hour and minute in 5-minute increments
   - **Duration**: Length of event in minutes
   - **Weekly Repeat**: Check to create recurring events for 12 weeks
3. Click "Create" to save

### Editing Events
1. Click on an existing event
2. Modify any fields (except recurring status)
3. Click "Update" to save or "Delete" to remove

### Deleting Recurring Events
- When deleting a recurring event, you'll be prompted to:
  - Delete only this occurrence, or
  - Delete all occurrences in the series

### Drag & Drop
- Click and hold an event
- Drag it to a different day
- Release to update the event's date

### Navigation
- **Previous/Next**: Navigate between months
- **Today**: Jump back to current month

## 📁 Project Structure

```
src/
├── components/          # React components
│   ├── CalendarDay.tsx    # Individual day cell
│   ├── EventModal.tsx     # Create/edit event modal
│   └── MonthView.tsx      # Main calendar grid
├── hooks/              # Custom React hooks
│   └── useCalendarEvents.ts
├── services/           # Data layer (localStorage)
│   └── eventService.ts
├── types/              # TypeScript type definitions
│   └── index.ts
├── utils/              # Validation utilities
│   └── validation.ts
├── App.tsx             # Root component
└── main.tsx            # Application entry point
```

## 🔧 Architecture Highlights

### Clean Separation of Concerns
- **Components**: Pure presentational logic
- **Hooks**: State management and business logic
- **Services**: Data persistence (easily swappable with API)
- **Utils**: Pure validation functions

### Type Safety
- Full TypeScript coverage
- Strict type checking enabled
- Type-only imports for clean module boundaries

### Extensibility
- `eventService.ts` abstraction makes it trivial to replace localStorage with REST API
- Component boundaries prepared for adding week/day views
- Validation layer separates business rules from UI

## 🧪 Validation Rules

- Event title: Required, max 100 characters
- Date: Valid ISO format (YYYY-MM-DD)
- Time: Valid 24-hour format (HH:MM)
- Duration: Minimum 5 minutes, must be multiple of 5
- Overlap: Events cannot overlap on the same day
- Recurring: Generates 12 weekly occurrences

## ⚡ Performance Optimizations

- `useMemo` for calendar day calculations
- Event grouping by date for O(1) lookups
- Minimal re-renders with proper React key usage
- Code splitting ready with Vite

## 🎨 Styling

- TailwindCSS utility-first approach
- Responsive design
- Accessible focus states
- Visual feedback for drag operations
- Color-coded availability (green = available slots)

## 🔮 Future Enhancements

The codebase is structured to easily add:
- Week and day views (component boundaries already clean)
- Backend API integration (swap `eventService` implementation)
- Event categories and colors
- Search and filtering
- Export to iCal/Google Calendar
- User authentication
- Shared calendars

## 📝 Development Notes

- ESLint configured with strict rules
- Prettier for code formatting
- All components use functional programming patterns
- Comprehensive inline documentation
- Input validation at multiple layers

## ✅ Verification

The application has been tested and verified to:
- ✅ Start without errors
- ✅ Display month calendar grid correctly
- ✅ Create, edit, and delete events
- ✅ Validate all inputs
- ✅ Prevent event overlaps
- ✅ Support drag-and-drop
- ✅ Persist data in localStorage
- ✅ Handle recurring events

## 🐛 Troubleshooting

If you encounter issues:

1. **Dependencies not installing**: Delete `node_modules` and `package-lock.json`, then run `npm install`
2. **Dev server won't start**: Ensure port 5173 is available
3. **Type errors**: Run `npm run build` to see detailed TypeScript errors
4. **Modal not working**: Check browser console for accessibility warnings

## 📄 License

This project is open source and available for educational and commercial use.

---

**Built with ❤️ using modern web technologies**
