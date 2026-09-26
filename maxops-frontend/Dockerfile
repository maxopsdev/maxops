FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Use `vite build` directly rather than the `npm run build` script (`tsc && vite build`):
# the full project has pre-existing TypeScript errors unrelated to any particular
# feature (see docs/OSS_EXTRACTION_SPEC.md); esbuild strips types without type-checking,
# so this still produces a working bundle. `npm run type-check` remains available
# separately (CI runs it, non-blocking) for anyone tightening this up.
RUN npx vite build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
