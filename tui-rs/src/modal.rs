//! Alternate-screen setup and teardown for the modal pickers.
//!
//! Each picker owns the alternate screen while it runs. That handover was
//! copied into all six run loops and two of them had drifted: `theme_picker`
//! and `preset_picker` forgot to `clear()` the fresh terminal, so the first
//! `draw()` could diff to nothing and leave the modal invisible. Keep the
//! sequence in one place so the next picker cannot miss a step.

use std::io;

use ratatui::layout::Rect;

/// A terminal on the alternate screen with the cursor hidden.
pub type ModalTerminal = ratatui::Terminal<ratatui::backend::CrosstermBackend<io::Stdout>>;

/// Enter the alternate screen and build the modal's terminal, cleared and
/// ready for its first `draw()`.
pub fn enter_modal_screen() -> io::Result<ModalTerminal> {
    crossterm::execute!(io::stdout(), crossterm::terminal::EnterAlternateScreen)?;
    crossterm::terminal::enable_raw_mode()?;
    let backend = ratatui::backend::CrosstermBackend::new(io::stdout());
    let mut terminal = ratatui::Terminal::new(backend)?;
    terminal.hide_cursor()?;
    terminal.clear()?;
    Ok(terminal)
}

/// Leave the alternate screen. Whether the app then quits is the caller's call.
pub fn leave_modal_screen() {
    let _ = crossterm::execute!(io::stdout(), crossterm::terminal::LeaveAlternateScreen);
}

/// A `width` x `height` rectangle centred in `area`.
///
/// Every picker centres its popup (and its small prompt dialog) the same way;
/// the arithmetic was copied at each site. `area` is the whole frame in every
/// caller, so this is behaviour-identical, but including `area.x`/`area.y`
/// keeps the helper honest if it is ever handed a sub-rectangle.
pub fn centered_rect(area: Rect, width: u16, height: u16) -> Rect {
    Rect {
        x: area.x + area.width.saturating_sub(width) / 2,
        y: area.y + area.height.saturating_sub(height) / 2,
        width,
        height,
    }
}

/// Move a list cursor up one, wrapping from the top to the bottom.
///
/// Each picker reimplemented the same wraparound; the only real difference was
/// whether the length came from a parameter or from the filtered list. The
/// `len == 0` case parks the cursor at 0, matching the old code; every list
/// mutation clamps the cursor, so a stale non-zero cursor with an empty list is
/// not reachable.
pub fn move_cursor_up(selected: &mut usize, len: usize) {
    if len == 0 {
        *selected = 0;
    } else if *selected > 0 {
        *selected -= 1;
    } else {
        *selected = len - 1;
    }
}

/// Move a list cursor down one, wrapping from the bottom back to the top.
pub fn move_cursor_down(selected: &mut usize, len: usize) {
    if len == 0 {
        *selected = 0;
    } else if *selected + 1 < len {
        *selected += 1;
    } else {
        *selected = 0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cursor_wraps_and_parks_on_an_empty_list() {
        let mut c = 0;
        move_cursor_up(&mut c, 3);
        assert_eq!(c, 2, "up from the top wraps to the bottom");
        move_cursor_down(&mut c, 3);
        assert_eq!(c, 0, "down from the bottom wraps to the top");
        move_cursor_down(&mut c, 3);
        assert_eq!(c, 1);

        move_cursor_up(&mut c, 0);
        assert_eq!(c, 0, "an empty list parks rather than underflows");
        move_cursor_down(&mut c, 0);
        assert_eq!(c, 0);
    }
}
