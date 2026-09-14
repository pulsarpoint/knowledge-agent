import { index, route, type RouteConfig } from "@react-router/dev/routes";

export default [
  index("./routes/home.tsx"),
  route(":app", "./routes/app.tsx"),
  route(":app/doc/*", "./routes/doc.tsx"),
  route(":app/search", "./routes/search.tsx"),
  route(":app/gaps", "./routes/gaps.tsx"),
  route(":app/chat/:key?", "./routes/chat.tsx"),
] satisfies RouteConfig;
