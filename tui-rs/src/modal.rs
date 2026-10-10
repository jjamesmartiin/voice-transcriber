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
