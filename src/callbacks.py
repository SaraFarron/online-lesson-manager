class RescheduleCallback:
    base = "reschedule/"
    choose_lesson = f"{base}choose_lesson/"
    choose_lesson_sl = f"{choose_lesson}sl/"
    choose_lesson_rs = f"{choose_lesson}rs/"
    choose_lesson_ls = f"{choose_lesson}ls/"
    choose_sl_entity = f"{base}choose_entity/"
    rm_cancel = f"{base}rm_cancel/"
    choose_date = f"{base}choose_date/"
    choose_time = f"{base}choose_time/"


class AddLessonCallback:
    base = "add_lesson/"
    choose_date = f"{base}choose_date/"
    choose_time = f"{base}choose_time/"
    finish = f"{base}finish/"


class CheckNotifyCallbacks:
    check_notify = "check_notify/"