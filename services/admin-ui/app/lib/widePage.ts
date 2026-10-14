import { createContext, useContext, useEffect } from "react";

/** The admin layout's switch for a page that needs the full width (the document page with Ask agent open). Outside the
 * layout it does nothing. */
export const WidePageContext = createContext<(on: boolean) => void>(() => {});

/** Asks the layout for the full width while `on`, and gives it back when it turns off or the page goes away. */
export function useWidePage(on: boolean) {
  const setWide = useContext(WidePageContext);
  useEffect(() => {
    setWide(on);
    return () => setWide(false);
  }, [on, setWide]);
}
