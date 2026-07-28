# Online Lesson Manager

Service for students and teachers to manage lesson schedule.

## Requirements

Service is available by at least 2 interfaces - telegram bot and web page. 
Users are able to register in service as students or teachers.

Teachers story:
- set start and end of working hours
- set max number of lessons per day
- set weekdays
- set breaks between lessons
- set one-time or regular temporary events (manually placing lessons, non-working hours, going on vacations)
- add and remove students
- add homework for users and lessons
- send notifications

Students story:
- crud lessons
- upload homework
- set one-time events (going on vacations)

## Functionality

Auth Service:
- sign up
- login
- recover
- roles

Schedule Service:
- crud lessons
  - changing lesson triggers change in Timetable
- crud breaks
  - changing break triggers change in Timetable
- crud vacations
  - changing vacation triggers change in Timetable
- lessons, breaks and vacations are linked to teacher and student or to teacher
- Timetable stores all events that block creation of new ones on certain time period for teacher

Homework Service:
- crud homework
- homework is linked to teacher and student, sometimes to lesson
- homework may carry a file (image, pdf, text, docx, markdown)

Notifications Service:
- crud notifications
  - automatic notifications (before lesson, at start of the day, undone homework)
  - manual notifications with ability to choose recievers


## Optional to develop
- integration with singularity, google calendar
- statistics on lessons for teachers
- vk bot
- whatsapp bot


