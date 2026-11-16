// Apply the saved theme before first paint to avoid a flash of the wrong theme.
try {
  var p = JSON.parse(localStorage.getItem("crr:prefs") || "{}");
  if (p.theme === "light") document.documentElement.dataset.theme = "light";
} catch (e) {}
