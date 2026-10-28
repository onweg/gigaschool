// Шрифты лежат в сборке, а не грузятся с fonts.googleapis.com: решение для
// государственной академии не должно ходить на чужие серверы при каждом
// открытии страницы, и в закрытом контуре интерфейс выглядит как задумано.
// Подключаем ровно те начертания и наборы символов, что используем.
import "@fontsource/inter/cyrillic-400.css";
import "@fontsource/inter/cyrillic-500.css";
import "@fontsource/inter/cyrillic-600.css";
import "@fontsource/inter/cyrillic-700.css";
import "@fontsource/inter/latin-400.css";
import "@fontsource/inter/latin-500.css";
import "@fontsource/inter/latin-600.css";
import "@fontsource/inter/latin-700.css";
import "@fontsource/manrope/cyrillic-400.css";
import "@fontsource/manrope/cyrillic-500.css";
import "@fontsource/manrope/cyrillic-600.css";
import "@fontsource/manrope/cyrillic-700.css";
import "@fontsource/manrope/latin-400.css";
import "@fontsource/manrope/latin-500.css";
import "@fontsource/manrope/latin-600.css";
import "@fontsource/manrope/latin-700.css";
import "@fontsource/jetbrains-mono/cyrillic-400.css";
import "@fontsource/jetbrains-mono/cyrillic-500.css";
import "@fontsource/jetbrains-mono/latin-400.css";
import "@fontsource/jetbrains-mono/latin-500.css";
import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";
import "./workspace.css";
import "./review.css";
import "./mobile.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
